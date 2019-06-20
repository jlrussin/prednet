#!/bin/bash
#SBATCH --qos=blanca-ccn
#SBATCH --mem=32G
#SBATCH --time=72:00:00
#SBATCH -c 12

export HOME=`getent passwd $USER | cut -d':' -f6`
export PYTHONUNBUFFERED=1
echo Running on $HOSTNAME

ml git

source /pl/active/ccnlab/conda/etc/profile.d/conda.sh
conda activate pytorch_source

python train.py \
--num_iters 100 \
--out_data_file time_test.json \
--record_loss_every 1
