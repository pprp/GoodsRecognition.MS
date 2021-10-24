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
"""train ShuffleNetV1"""
import os
import time
from mindspore import context
from mindspore import Tensor
from mindspore.common import set_seed
from mindspore.nn.optim.momentum import Momentum
from mindspore.context import ParallelMode
from mindspore.train.model import Model
from mindspore.train.callback import ModelCheckpoint, CheckpointConfig, TimeMonitor, LossMonitor
from mindspore.train.serialization import load_checkpoint, load_param_into_net
from mindspore.communication.management import init, get_rank, get_group_size
from mindspore.train.loss_scale_manager import FixedLossScaleManager
from src.lr_generator import get_lr
from src.shufflenetv1 import ShuffleNetV1
from src.dataset import create_dataset
from src.crossentropysmooth import CrossEntropySmooth
from src.model_utils.config import config
from src.model_utils.moxing_adapter import moxing_wrapper
from src.model_utils.device_adapter import get_device_id

set_seed(1)


def modelarts_pre_process():
    pass


@moxing_wrapper(pre_process=modelarts_pre_process)
def train():
    context.set_context(mode=context.GRAPH_MODE, device_target=config.device_target, save_graphs=False)

    # init distributed
    if config.is_distributed:
        if os.getenv('DEVICE_ID', "not_set").isdigit():
            context.set_context(device_id=get_device_id())
        init()
        rank = get_rank()
        group_size = get_group_size()
        parallel_mode = ParallelMode.DATA_PARALLEL
        context.set_auto_parallel_context(parallel_mode=parallel_mode, device_num=group_size, gradients_mean=True)
    else:
        rank = 0
        group_size = 1
        context.set_context(device_id=config.device_id)

    # define network
    net = ShuffleNetV1(model_size=config.model_size, n_class=config.num_classes)

    # define loss
    loss = CrossEntropySmooth(sparse=True, reduction="mean", smooth_factor=config.label_smooth_factor,
                              num_classes=config.num_classes)

    # define dataset
    dataset = create_dataset(config.train_dataset_path, do_train=True, device_num=group_size, rank=rank)
    batches_per_epoch = dataset.get_dataset_size()

    # resume
    if config.resume:
        ckpt = load_checkpoint(config.resume)
        load_param_into_net(net, ckpt)

    # get learning rate
    lr = get_lr(lr_init=config.lr_init, lr_end=config.lr_end, lr_max=config.lr_max, warmup_epochs=config.warmup_epochs,
                total_epochs=config.epoch_size, steps_per_epoch=batches_per_epoch, lr_decay_mode=config.decay_method)
    lr = Tensor(lr)
    # define optimization
    optimizer = Momentum(params=net.trainable_params(), learning_rate=lr, momentum=config.momentum,
                         weight_decay=config.weight_decay, loss_scale=config.loss_scale)

    # model
    loss_scale_manager = FixedLossScaleManager(config.loss_scale, drop_overflow_update=False)
    model = Model(net, loss_fn=loss, optimizer=optimizer, amp_level=config.amp_level,
                  loss_scale_manager=loss_scale_manager)

    # define callbacks
    cb = [TimeMonitor(), LossMonitor()]
    if config.save_checkpoint:
        save_ckpt_path = config.save_ckpt_path
        config_ck = CheckpointConfig(save_checkpoint_steps=config.save_checkpoint_epochs * batches_per_epoch,
                                     keep_checkpoint_max=config.keep_checkpoint_max)
        ckpt_cb = ModelCheckpoint("shufflenetv1", directory=save_ckpt_path, config=config_ck)

    print("============== Starting Training ==============")
    start_time = time.time()
    # begin train
    if config.is_distributed:
        if rank == 0:
            cb += [ckpt_cb]
    else:
        cb += [ckpt_cb]
    model.train(config.epoch_size, dataset, callbacks=cb, dataset_sink_mode=True)
    print("time: ", (time.time() - start_time) * 1000)
    print("============== Train Success ==============")


if __name__ == '__main__':
    train()
