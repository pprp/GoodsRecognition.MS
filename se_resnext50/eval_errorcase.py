# -*- coding:utf-8 -*-
# Copyright 2021 Huawei Technologies Co., Ltd
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
"""Eval"""
import os
import time
import datetime
import glob
import numpy as np
import mindspore.nn as nn

from mindspore import Tensor, context
from mindspore.context import ParallelMode
from mindspore.communication.management import init, get_rank, get_group_size, release
from mindspore.ops import operations as P
from mindspore.ops import functional as F
from mindspore.common import dtype as mstype

from src.models import build_network
from src.utils.logging import get_logger
from src.utils.auto_mixed_precision import auto_mixed_precision
from src.utils.var_init import load_pretrain_model
# from src.image_classification import get_network
from src.py_dataset import classification_dataset
from src.config import config
from src.model_utils.moxing_adapter import moxing_wrapper
from collections import defaultdict


class ParameterReduce(nn.Cell):
    """ParameterReduce"""

    def __init__(self):
        super(ParameterReduce, self).__init__()
        self.cast = P.Cast()
        self.reduce = P.AllReduce()

    def construct(self, x):
        one = self.cast(F.scalar_to_array(1.0), mstype.float32)
        out = x * one
        ret = self.reduce(out)
        return ret


def set_parameters():
    """set_parameters"""
    if config.run_distribute:
        init()
        config.rank = get_rank()
        config.group_size = get_group_size()
    else:
        config.rank = 0
        config.group_size = 1

    config.outputs_dir = os.path.join(config.log_path,
                                      datetime.datetime.now().strftime('%Y-%m-%d_time_%H_%M_%S'))

    config.logger = get_logger(config.outputs_dir, config.rank)
    return config


def get_top5_acc(top5_arg, gt_class):
    sub_count = 0
    for top5, gt in zip(top5_arg, gt_class):
        if gt in top5:
            sub_count += 1
    return sub_count


def get_result(model, top1_correct, top5_correct, img_tot):
    """calculate top1 and top5 value."""
    results = [[top1_correct], [top5_correct], [img_tot]]
    config.logger.info('before results=%s', results)
    if config.run_distribute:
        model_md5 = model.replace('/', '')
        tmp_dir = '/cache'
        if not os.path.exists(tmp_dir):
            os.mkdir(tmp_dir)
        top1_correct_npy = '/cache/top1_rank_{}_{}.npy'.format(config.rank, model_md5)
        top5_correct_npy = '/cache/top5_rank_{}_{}.npy'.format(config.rank, model_md5)
        img_tot_npy = '/cache/img_tot_rank_{}_{}.npy'.format(config.rank, model_md5)
        np.save(top1_correct_npy, top1_correct)
        np.save(top5_correct_npy, top5_correct)
        np.save(img_tot_npy, img_tot)
        while True:
            rank_ok = True
            for other_rank in range(config.group_size):
                top1_correct_npy = '/cache/top1_rank_{}_{}.npy'.format(other_rank, model_md5)
                top5_correct_npy = '/cache/top5_rank_{}_{}.npy'.format(other_rank, model_md5)
                img_tot_npy = '/cache/img_tot_rank_{}_{}.npy'.format(other_rank, model_md5)
                if not os.path.exists(top1_correct_npy) or not os.path.exists(top5_correct_npy) or \
                        not os.path.exists(img_tot_npy):
                    rank_ok = False
            if rank_ok:
                break

        top1_correct_all = 0
        top5_correct_all = 0
        img_tot_all = 0
        for other_rank in range(config.group_size):
            top1_correct_npy = '/cache/top1_rank_{}_{}.npy'.format(other_rank, model_md5)
            top5_correct_npy = '/cache/top5_rank_{}_{}.npy'.format(other_rank, model_md5)
            img_tot_npy = '/cache/img_tot_rank_{}_{}.npy'.format(other_rank, model_md5)
            top1_correct_all += np.load(top1_correct_npy)
            top5_correct_all += np.load(top5_correct_npy)
            img_tot_all += np.load(img_tot_npy)
        results = [[top1_correct_all], [top5_correct_all], [img_tot_all]]
        results = np.array(results)
    else:
        results = np.array(results)

    config.logger.info('after results=%s', results)
    return results


# IMG_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.ppm', '.bmp', '.pgm', '.tif', '.tiff', '.webp')


# def find_images_and_targets(folder, types=IMG_EXTENSIONS, class_to_idx=None, leaf_name_only=True):
#     labels = set()
#     for root, subdirs, files in os.walk(folder, topdown=False, followlinks=True):
#         rel_path = os.path.relpath(root, folder) if (root != folder) else ''
#         label = os.path.basename(rel_path) if leaf_name_only else rel_path.replace(os.path.sep, '_')
#         for f in files:
#             base, ext = os.path.splitext(f)
#             if ext.lower() in types:
#                 labels.add(label)
#     unique_labels = set(labels)
#     sorted_labels = sorted(list(unique_labels))
#     class_to_idx = {c: idx for idx, c in enumerate(sorted_labels)}

#     return class_to_idx


# class_indexing = find_images_and_targets(config.eval_data_path)
# class_index_name_dic = {value: key for key, value in class_indexing.items()}


@moxing_wrapper()
def test():
    """test"""
    set_parameters()
    context.set_context(mode=context.GRAPH_MODE,
                        device_target=config.device_target, save_graphs=False)
    if os.getenv('DEVICE_ID', "not_set").isdigit():
        context.set_context(device_id=int(os.getenv('DEVICE_ID')))

    # init distributed
    if config.run_distribute:
        parallel_mode = ParallelMode.DATA_PARALLEL
        context.set_auto_parallel_context(parallel_mode=parallel_mode, device_num=config.group_size,
                                          gradients_mean=True)

    config.logger.save_args(config)

    # network
    config.logger.important_info('start create network')
    if os.path.isdir(config.checkpoint_file_path):
        models = list(glob.glob(os.path.join(config.checkpoint_file_path, '*.ckpt')))
        print(models)
        if config.checkpoint_file_path:
            f = lambda x: -1 * int(os.path.splitext(os.path.split(x)[-1])[0].split('-')[-1].split('_')[0])
        else:
            f = lambda x: -1 * int(os.path.splitext(os.path.split(x)[-1])[0].split('_')[-1])
        config.models = sorted(models, key=f)
    else:
        config.models = [config.checkpoint_file_path, ]

    for model in config.models:
        de_dataset = classification_dataset(config.eval_data_path, image_size=config.image_size,
                                            per_batch_size=config.per_batch_size,
                                            max_epoch=1, rank=config.rank, group_size=config.group_size,
                                            mode='eval')

        eval_dataloader = de_dataset.create_tuple_iterator(output_numpy=True, num_epochs=1)
        network = build_network(config.model_name, config.num_classes)

        load_pretrain_model(model, network, config)

        img_tot = 0
        top1_correct = 0
        top5_correct = 0
        if config.device_target == "Ascend":
            network.to_float(mstype.float16)
        else:
            auto_mixed_precision(network)
        network.set_train(False)
        t_end = time.time()
        it = 0

        class_indexing = de_dataset.get_class_indexing()   #find_images_and_targets(config.eval_data_path)
        class_index_name_dic = {value: key for key, value in class_indexing.items()}
        
        err_cls_num_dic = defaultdict(int)
        correct_cls_num_dic = defaultdict(int)
        for data, gt_classes in eval_dataloader:
            output = network(Tensor(data, mstype.float32))
            output = output.asnumpy()

            top1_output = np.argmax(output, (-1))
            top5_output = np.argsort(output)[:, -5:]

            for top1_class, gt_class in zip(top1_output, gt_classes):
                if top1_class != gt_class:
                    err_cls_num_dic[gt_class] += 1
                else:
                    correct_cls_num_dic[gt_class] += 1

            t1_correct = np.equal(top1_output, gt_classes).sum()
            top1_correct += t1_correct
            top5_correct += get_top5_acc(top5_output, gt_classes)
            img_tot += config.per_batch_size

            if config.rank == 0 and it == 0:
                t_end = time.time()
                it = 1

        err_cls_num_lis = sorted(list(err_cls_num_dic.items()), key=lambda x: x[1], reverse=True)
        lis = [(class_index_name_dic[i[0]], i[1]) for i in err_cls_num_lis]
        print(lis)

        all_keys = set(correct_cls_num_dic) | set(err_cls_num_dic)

        res = {}
        for key in all_keys:
            correct_num = correct_cls_num_dic[key]
            err_num = err_cls_num_dic[key]

            res[class_index_name_dic[key]] = correct_num / (correct_num + err_num)

        res = sorted(list(res.items()), key=lambda x: x[1], reverse=True)
        print(res)

        if config.rank == 0:
            time_used = time.time() - t_end
            fps = (img_tot - config.per_batch_size) * config.group_size / time_used
            config.logger.info('Inference Performance: {:.2f} img/sec'.format(fps))
        results = get_result(model, top1_correct, top5_correct, img_tot)
        top1_correct = results[0, 0]
        top5_correct = results[1, 0]
        img_tot = results[2, 0]
        acc1 = 100.0 * top1_correct / img_tot
        acc5 = 100.0 * top5_correct / img_tot
        config.logger.info('after allreduce eval: top1_correct={}, tot={},'
                           'acc={:.2f}%(TOP1)'.format(top1_correct, img_tot, acc1))
        config.logger.info('after allreduce eval: top5_correct={}, tot={},'
                           'acc={:.2f}%(TOP5)'.format(top5_correct, img_tot, acc5))
    if config.run_distribute:
        release()


if __name__ == "__main__":
    test()
