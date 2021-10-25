# Copyright 2020 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""evaluate imagenet"""
import argparse

import mindspore.nn as nn
from mindspore import context
from mindspore.train.model import Model
from mindspore.train.serialization import load_checkpoint, load_param_into_net

from src.config import dataset_config
from src.dataset import create_dataset_val
from src.efficientnet import efficientnet_b0
from src.loss import LabelSmoothingCrossEntropy

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='image classification evaluation')
    parser.add_argument('--checkpoint', type=str, required=True, help='checkpoint of efficientnet (Default: None)')
    parser.add_argument('--data_path', type=str, required=True, help='Dataset path')
    parser.add_argument('--dataset', type=str, default='ImageNet', choices=['ImageNet', 'CIFAR10'],
                        help='ImageNet or CIFAR10')
    parser.add_argument('--platform', type=str, default='GPU', choices=('GPU', 'CPU'), help='run platform')
    args_opt = parser.parse_args()
    print(args_opt)

    context.set_context(mode=context.GRAPH_MODE, device_target=args_opt.platform)

    dataset_type = args_opt.dataset.lower()
    cfg = dataset_config[dataset_type].cfg

    net = efficientnet_b0(num_classes=cfg.num_classes,
                          cfg=dataset_config[dataset_type],
                          drop_rate=cfg.drop,
                          drop_connect_rate=cfg.drop_connect,
                          global_pool=cfg.gp,
                          bn_tf=cfg.bn_tf,
                          )

    ckpt = load_checkpoint(args_opt.checkpoint)
    load_param_into_net(net, ckpt)
    net.set_train(False)
    val_data_url = args_opt.data_path
    dataset = create_dataset_val(dataset_type, val_data_url, cfg.batch_size, workers=cfg.workers, distributed=False)
    loss = LabelSmoothingCrossEntropy(smooth_factor=cfg.smoothing, num_classes=cfg.num_classes)
    eval_metrics = {'Loss': nn.Loss(),
                    'Top1-Acc': nn.Top1CategoricalAccuracy(),
                    'Top5-Acc': nn.Top5CategoricalAccuracy()}
    model = Model(net, loss, optimizer=None, metrics=eval_metrics)

    dataset_sink_mode = args_opt.platform != "CPU"

    metrics = model.eval(dataset, dataset_sink_mode=dataset_sink_mode)

    print("metric: ", metrics)
