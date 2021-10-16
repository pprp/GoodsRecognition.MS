#!/bin/bash
export DEVICE_ID=5
export SLOG_PRINT_TO_STDOUT=0
train_path=/PATH/TO/EXPERIMENTS_DIR
train_code_path=/PATH/TO/MODEL_ZOO_CODE

if [ -d ${train_path} ]; then
  rm -rf ${train_path}
fi
mkdir -p ${train_path}
mkdir ${train_path}/device${DEVICE_ID}
mkdir ${train_path}/ckpt
cd ${train_path}/device${DEVICE_ID} || exit

python ${train_code_path}/train.py --data_file=/PATH/TO/MINDRECORD_NAME  \
                    --train_dir=${train_path}/ckpt  \
                    --train_epochs=200  \
                    --batch_size=32  \
                    --crop_size=513  \
                    --base_lr=0.015  \
                    --lr_type=cos  \
                    --min_scale=0.5  \
                    --max_scale=2.0  \
                    --ignore_label=255  \
                    --num_classes=21  \
                    --model=DeepLabV3plus_s16  \
                    --ckpt_pre_trained=/PATH/TO/PRETRAIN_MODEL  \
                    --save_steps=1500  \
                    --keep_checkpoint_max=200 >log 2>&1 &