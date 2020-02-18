import torch
from torch import nn
import torch.nn.functional as F

from lib import spec_utils


class CBAM(nn.Module):

    def __init__(self, ch, ratio=16):
        super(CBAM, self).__init__()
        self.sqz = nn.Linear(ch, ch // ratio)
        self.ext = nn.Linear(ch // ratio, ch)
        self.conv = nn.Conv2d(None, 1, 3, 1, 1, bias=False)

    def __call__(self, x, e=None):
        gap = x.mean(dim=(2, 3))
        gmp = x.max(dim=(2, 3))
        gap = self.ext(F.relu(self.sqz(gap)))
        gmp = self.ext(F.relu(self.sqz(gmp)))
        x = F.sigmoid(gap + gmp)[:, :, None, None] * x

        gap = x.mean(dim=1)[:, None]
        gmp = x.max(dim=1)[:, None]
        h = self.conv(torch.cat([gap, gmp], dim=1))
        h = F.sigmoid(h) * x

        return h


class Conv2DBNActiv(nn.Module):

    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, dilation=1, activ=nn.ReLU):
        super(Conv2DBNActiv, self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(
                nin, nout,
                kernel_size=ksize,
                stride=stride,
                padding=pad,
                dilation=dilation,
                bias=False),
            nn.BatchNorm2d(nout),
            activ()
        )

    def __call__(self, x):
        return self.conv(x)


class SeperableConv2DBNActiv(nn.Module):

    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, dilation=1, activ=nn.ReLU):
        super(SeperableConv2DBNActiv, self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(
                nin, nin,
                kernel_size=ksize,
                stride=stride,
                padding=pad,
                dilation=dilation,
                groups=nin,
                bias=False),
            nn.Conv2d(
                nin, nout,
                kernel_size=1,
                bias=False),
            nn.BatchNorm2d(nout),
            activ()
        )

    def __call__(self, x):
        return self.conv(x)


class Encoder(nn.Module):

    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, activ=nn.LeakyReLU):
        super(Encoder, self).__init__()
        self.conv1 = Conv2DBNActiv(
            nin, nout, ksize, 1, pad, activ=activ)
        self.conv2 = Conv2DBNActiv(
            nout, nout, ksize, stride, pad, activ=activ)

    def __call__(self, x):
        skip = self.conv1(x)
        h = self.conv2(skip)

        return h, skip


class Decoder(nn.Module):

    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, dropout=False):
        super(Decoder, self).__init__()
        self.conv = Conv2DBNActiv(nin, nout, ksize, 1, pad)
        self.dropout = nn.Dropout2d(0.1) if dropout else None

    def __call__(self, x, skip=None):
        x = F.interpolate(x, scale_factor=2, mode='bilinear', align_corners=True)
        if skip is not None:
            x = spec_utils.crop_center(x, skip)
        h = self.conv(x)

        if self.dropout is not None:
            h = self.dropout(h)

        return h


class DecoderV2(nn.Module):

    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, dropout=False):
        super(DecoderV2, self).__init__()
        self.conv1 = Conv2DBNActiv(nin, nout, 1, 1, 0)
        self.conv2 = Conv2DBNActiv(nout, nout, ksize, 1, pad)
        self.dropout = nn.Dropout2d(0.1) if dropout else None

    def __call__(self, x, skip=None):
        if skip is not None:
            x = torch.cat([x, skip], dim=1)
        h = self.conv1(x)
        h = F.interpolate(h, scale_factor=2, mode='bilinear', align_corners=True)
        h = self.conv2(h)

        if self.dropout is not None:
            h = self.dropout(h)

        return h


class ASPPModule(nn.Module):

    def __init__(self, nin, dilations=(4, 8, 16)):
        super(ASPPModule, self).__init__()
        self.conv1 = nn.Sequential(
            nn.AdaptiveAvgPool2d((1, 1)),
            Conv2DBNActiv(nin, nin, 1, 1, 0, activ=nn.LeakyReLU)
        )
        self.conv2 = Conv2DBNActiv(
            nin, nin, 1, 1, 0, activ=nn.LeakyReLU)
        self.conv3 = SeperableConv2DBNActiv(
            nin, nin, 3, 1, dilations[0], dilations[0], activ=nn.LeakyReLU)
        self.conv4 = SeperableConv2DBNActiv(
            nin, nin, 3, 1, dilations[1], dilations[1], activ=nn.LeakyReLU)
        self.conv5 = SeperableConv2DBNActiv(
            nin, nin, 3, 1, dilations[2], dilations[2], activ=nn.LeakyReLU)
        self.bottleneck = nn.Sequential(
            Conv2DBNActiv(nin * 5, nin, 1, 1, 0, activ=nn.LeakyReLU),
            nn.Dropout2d(0.1)
        )

    def forward(self, x):
        _, _, h, w = x.size()
        feat1 = F.interpolate(self.conv1(x), size=(h, w), mode='bilinear', align_corners=True)
        feat2 = self.conv2(x)
        feat3 = self.conv3(x)
        feat4 = self.conv4(x)
        feat5 = self.conv5(x)
        out = torch.cat((feat1, feat2, feat3, feat4, feat5), dim=1)
        bottle = self.bottleneck(out)
        return bottle