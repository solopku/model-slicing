import torch
import torch.nn as nn
from .dynamic_conv2d import DynamicConv2d
from torch.distributions import normal
import math

def dynamic_conv3x3(in_planes, out_planes, stride=1):
    "3x3 convolution with padding"
    return DynamicConv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=1, bias=False)

class BasicBlock(nn.Module):
    outchannel_ratio = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.bn1 = nn.BatchNorm2d(inplanes)
        self.conv1 = dynamic_conv3x3(inplanes, planes, stride)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv2 = dynamic_conv3x3(planes, planes)
        self.bn3 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, input):
        # wrap input/output due to nn.Sequential
        x, keep_rate = input

        out = self.bn1(x)
        out = self.conv1(out, keep_rate)
        out = self.bn2(out)
        out = self.relu(out)
        out = self.conv2(out, keep_rate)
        out = self.bn3(out)

        if self.downsample is not None:
            shortcut = self.downsample(x)
            featuremap_size = shortcut.size()[2:4]
        else:
            shortcut = x
            featuremap_size = out.size()[2:4]

        batch_size = out.size()[0]
        residual_channel = out.size()[1]
        shortcut_channel = shortcut.size()[1]

        if residual_channel != shortcut_channel:
            padding = torch.zeros((batch_size, residual_channel - shortcut_channel, featuremap_size[0],
                                       featuremap_size[1]), device=shortcut.device)
            out += torch.cat((shortcut, padding), 1)
        else:
            out += shortcut

        return (out, keep_rate)

class Bottleneck(nn.Module):
    outchannel_ratio = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.bn1 = nn.BatchNorm2d(inplanes)
        self.conv1 = DynamicConv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv2 = DynamicConv2d(planes, (planes * 1), kernel_size=3, stride=stride,
                               padding=1, bias=False)
        self.bn3 = nn.BatchNorm2d((planes * 1))
        self.conv3 = DynamicConv2d((planes * 1), planes * Bottleneck.outchannel_ratio, kernel_size=1, bias=False)
        self.bn4 = nn.BatchNorm2d(planes * Bottleneck.outchannel_ratio)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, input):
        # wrap input/output due to nn.Sequential
        x, keep_rate = input

        out = self.bn1(x)
        out = self.conv1(out, keep_rate)

        out = self.bn2(out)
        out = self.relu(out)
        out = self.conv2(out, keep_rate)

        out = self.bn3(out)
        out = self.relu(out)
        out = self.conv3(out, keep_rate)

        out = self.bn4(out)

        if self.downsample is not None:
            shortcut = self.downsample(x)
            featuremap_size = shortcut.size()[2:4]
        else:
            shortcut = x
            featuremap_size = out.size()[2:4]

        batch_size = out.size()[0]
        residual_channel = out.size()[1]
        shortcut_channel = shortcut.size()[1]

        if residual_channel != shortcut_channel:
            padding = torch.zeros((batch_size, residual_channel - shortcut_channel, featuremap_size[0],
                                   featuremap_size[1]), device=shortcut.device)
            out += torch.cat((shortcut, padding), 1)
        else:
            out += shortcut

        return (out, keep_rate)

class DynamicPyramidNet(nn.Module):
    def __init__(self, dataset, depth, alpha, num_classes, bottleneck=False):
        super(DynamicPyramidNet, self).__init__()
        # distribution to draw keep_rate
        self.dist = normal.Normal(1., 1. / 3.)

        self.dataset = dataset
        if self.dataset.startswith('cifar'):
            self.inplanes = 16
            if bottleneck == True:
                n = int((depth - 2) / 9)
                block = Bottleneck
            else:
                n = int((depth - 2) / 6)
                block = BasicBlock

            self.addrate = alpha / (3 * n * 1.0)

            self.input_featuremap_dim = self.inplanes
            self.conv1 = nn.Conv2d(3, self.input_featuremap_dim, kernel_size=3, stride=1, padding=1, bias=False)
            self.bn1 = nn.BatchNorm2d(self.input_featuremap_dim)

            self.featuremap_dim = self.input_featuremap_dim
            self.layer1 = self.pyramidal_make_layer(block, n)
            self.layer2 = self.pyramidal_make_layer(block, n, stride=2)
            self.layer3 = self.pyramidal_make_layer(block, n, stride=2)

            self.final_featuremap_dim = self.input_featuremap_dim
            self.bn_final = nn.BatchNorm2d(self.final_featuremap_dim)
            self.relu_final = nn.ReLU(inplace=True)
            self.avgpool = nn.AvgPool2d(8)
            self.fc = nn.Linear(self.final_featuremap_dim, num_classes)

        elif dataset == 'imagenet':
            blocks = {18: BasicBlock, 34: BasicBlock, 50: Bottleneck, 101: Bottleneck, 152: Bottleneck, 200: Bottleneck}
            layers = {18: [2, 2, 2, 2], 34: [3, 4, 6, 3], 50: [3, 4, 6, 3], 101: [3, 4, 23, 3], 152: [3, 8, 36, 3],
                      200: [3, 24, 36, 3]}

            if layers.get(depth) is None:
                if bottleneck == True:
                    blocks[depth] = Bottleneck
                    temp_cfg = int((depth - 2) / 12)
                else:
                    blocks[depth] = BasicBlock
                    temp_cfg = int((depth - 2) / 8)

                layers[depth] = [temp_cfg, temp_cfg, temp_cfg, temp_cfg]
                print('=> the layer configuration for each stage is set to', layers[depth])

            self.inplanes = 64
            self.addrate = alpha / (sum(layers[depth]) * 1.0)

            self.input_featuremap_dim = self.inplanes
            self.conv1 = nn.Conv2d(3, self.input_featuremap_dim, kernel_size=7, stride=2, padding=3, bias=False)
            self.bn1 = nn.BatchNorm2d(self.input_featuremap_dim)
            self.relu = nn.ReLU(inplace=True)
            self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

            self.featuremap_dim = self.input_featuremap_dim
            self.layer1 = self.pyramidal_make_layer(blocks[depth], layers[depth][0])
            self.layer2 = self.pyramidal_make_layer(blocks[depth], layers[depth][1], stride=2)
            self.layer3 = self.pyramidal_make_layer(blocks[depth], layers[depth][2], stride=2)
            self.layer4 = self.pyramidal_make_layer(blocks[depth], layers[depth][3], stride=2)

            self.final_featuremap_dim = self.input_featuremap_dim
            self.bn_final = nn.BatchNorm2d(self.final_featuremap_dim)
            self.relu_final = nn.ReLU(inplace=True)
            self.avgpool = nn.AvgPool2d(7)
            self.fc = nn.Linear(self.final_featuremap_dim, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def pyramidal_make_layer(self, block, block_depth, stride=1):
        downsample = None
        if stride != 1:  # or self.inplanes != int(round(featuremap_dim_1st)) * block.outchannel_ratio:
            downsample = nn.AvgPool2d((2, 2), stride=(2, 2), ceil_mode=True)

        layers = []
        self.featuremap_dim = self.featuremap_dim + self.addrate
        layers.append(block(self.input_featuremap_dim, int(round(self.featuremap_dim)), stride, downsample))
        for i in range(1, block_depth):
            temp_featuremap_dim = self.featuremap_dim + self.addrate
            layers.append(
                block(int(round(self.featuremap_dim)) * block.outchannel_ratio, int(round(temp_featuremap_dim)), 1))
            self.featuremap_dim = temp_featuremap_dim
        self.input_featuremap_dim = int(round(self.featuremap_dim)) * block.outchannel_ratio

        return nn.Sequential(*layers)

    def forward(self, x, keep_rate, progress=1.):

        if self.dataset == 'cifar10' or self.dataset == 'cifar100':
            x = self.conv1(x)
            x = self.bn1(x)

            # 1st place, wrap input due to nn.Sequential
            x = self.layer1((x, keep_rate))[0]
            x = self.layer2((x, keep_rate))[0]
            x = self.layer3((x, keep_rate))[0]

            x = self.bn_final(x)
            x = self.relu_final(x)
            x = self.avgpool(x)
            x = x.view(x.size(0), -1)
            # 2nd place, scale by 1./keep_rate
            # channel_num = round(x.size(1)*keep_rate)
            # if channel_num < x.size(1):   x *= float(x.size(1))/channel_num
            x = self.fc(x)

        # need revision here
        elif self.dataset == 'imagenet':
            x = self.conv1(x)
            x = self.bn1(x)
            x = self.relu(x)
            x = self.maxpool(x)

            x = self.layer1((x, keep_rate))[0]
            x = self.layer2((x, keep_rate))[0]
            x = self.layer3((x, keep_rate))[0]
            x = self.layer4((x, keep_rate))[0]

            x = self.bn_final(x)
            x = self.relu_final(x)
            x = self.avgpool(x)
            x = x.view(x.size(0), -1)
            x = self.fc(x)

        return x

def test():
    net = DynamicPyramidNet('cifar', 32, 300, 10, True)
    for module in net.modules():
        if isinstance(module, nn.Conv2d):# and module.kernel_size==(3,3):
            print(module)
            print(module. weight.size(), module.weight.nelement())

    # y = net(Variable(torch.randn(1,3,32,32)))
    # print(y.size())
    print('==> Number of params: {}'.format(
        sum([param.data.nelement() for param in net.parameters()])))
    print('marker')

# test()