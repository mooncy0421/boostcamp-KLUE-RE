import os
import pandas as pd
import torch
import sklearn
import numpy as np
from sklearn.metrics import accuracy_score, recall_score, precision_score, f1_score
from transformers import AutoTokenizer, AutoConfig, AutoModelForSequenceClassification, \
    Trainer, TrainingArguments, RobertaConfig, RobertaTokenizer, RobertaForSequenceClassification, BertTokenizer, EarlyStoppingCallback
from load_data import *
from metric import *
from model import *

import wandb
import random
from sklearn.model_selection import StratifiedKFold, StratifiedShuffleSplit, train_test_split
from torch.utils.data import Subset
from custom_trainer import CustomTrainer

def train(MODE="default", run_name="NoSetting"):
  seed_everything(1004)
  # load model and tokenizer
  MODEL_NAME = "klue/roberta-large"

  # sentence preprocessing type
  entity_tk_type = 'add_entity_type_punct_star'

  # valid set
  valid = False
  valid_size = 0.1

  # custom Trainer
  custom = True

  # model modification
  model_default = True

  # hard-voting ensemble
  ensemble = True

  tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
  num_added_sptoks = 0
  if MODE=="add_sptok":
      num_added_sptoks = tokenizer.add_special_tokens({"additional_special_tokens": ['[TP]', '[/TP]']})
  # TODO : [TP], [/TP] special token 추가할 경우

  DATA_PATH = '../../dataset/train/cleaned_train.csv'
  # TODO : train.csv 파일 경로

  # load dataset
  train_dataset = load_data(DATA_PATH,entity_tk_type)
  train_label = label_to_num(train_dataset['label'].values)
  tokenized_train = tokenized_dataset(train_dataset, tokenizer)
  RE_train_dataset = RE_Dataset(tokenized_train, train_label)


  if valid:
      RE_train_dataset, RE_dev_dataset = train_test_split(RE_train_dataset, test_size=valid_size,
                                                     shuffle=True, stratify=train_dataset['label'])
  else:
      RE_dev_dataset = RE_train_dataset

  device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

  print(device)
  # setting model hyperparameter
  model_config =  AutoConfig.from_pretrained(MODEL_NAME)
  model_config.num_labels = 30


  if model_default:
      model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, config=model_config)
  else:
      get_model(MODEL_NAME, tokenizer=tokenizer)
  print(model.config)
  model.parameters
  model.to(device)

  torch.cuda.empty_cache()

  output_dir = './results' # TODO : output_dir 설정
  label_smoothing_factor = 0.0 # TODO : label_smoothing factor

  wandb.init(
      project='KLUE',
      entity='miml',
      name=run_name
  )

  training_args = TrainingArguments(
      output_dir=output_dir,  # output directory
      save_total_limit=3,  # number of total save model.
      save_steps=1000,  # model saving step.
      num_train_epochs=4,  # total number of training epochs
      learning_rate=2e-5,  # learning_rate
      per_device_train_batch_size=16,  # batch size per device during training
      per_device_eval_batch_size=16,  # batch size for evaluation
      lr_scheduler_type='cosine', #SchedulerType LINEAR, COSINE, POLYNOMIAL...,
      warmup_steps=300,  # number of warmup steps for learning rate scheduler
      weight_decay=0.01,  # strength of weight decay
      logging_dir='./logs',  # directory for storing logs
      logging_steps=100,  # log saving step.
      evaluation_strategy='steps',  # evaluation strategy to adopt during training
      # `no`: No evaluation during training.
      # `steps`: Evaluate every `eval_steps`.
      # `epoch`: Evaluate every end of epoch.
      eval_steps=500,  # evaluation step.
      metric_for_best_model="micro f1 score",
      load_best_model_at_end=True,
      report_to="wandb",
      fp16=True,
      fp16_opt_level="O1",
      label_smoothing_factor=label_smoothing_factor
  )

  if custom:
      trainer = CustomTrainer(
          loss_name='LabelSmoothing',
          model=model,  # the instantiated 🤗 Transformers model to be trained
          args=training_args,  # training arguments, defined above
          train_dataset=RE_train_dataset,  # training dataset
          eval_dataset=RE_train_dataset,  # evaluation dataset
          compute_metrics=compute_metrics  # define metrics function
      )
  else:
      trainer = Trainer(
          model=model,  # the instantiated 🤗 Transformers model to be trained
          args=training_args,  # training arguments, defined above
          train_dataset=RE_train_dataset,  # training dataset
          eval_dataset=RE_dev_dataset,  # evaluation dataset
          compute_metrics=compute_metrics  # define metrics function
      )

  # Hard Voting Ensemble
  torch.cuda.empty_cache()
  if ensemble:
      train_val_split = StratifiedKFold(n_splits=3, shuffle=True, random_state=1004)
      idx = 0
      for train_idx, valid_idx in train_val_split.split(RE_train_dataset, RE_train_dataset.labels):
          idx += 1
          model_config = AutoConfig.from_pretrained(MODEL_NAME)
          model_config.num_labels = 30

          model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, config=model_config)
          model.resize_token_embeddings(tokenizer.vocab_size + num_added_sptoks)
          # TODO : MODE가 "add_sptok"여야지만 num_added_sptoks가 설정됨
          print(model.config)
          model.parameters
          model.to(device)
          train_subset = Subset(RE_train_dataset, train_idx)
          valid_subset = Subset(RE_train_dataset, valid_idx)
          # print(type(tokenized_train), tokenized_train)
          # print(type(train_subset.dataset),train_subset.dataset)
          # RE_train_dataset = RE_Dataset(train_subset.dataset.items(), train_subset.indices.items())
          # RE_valid_dataset = RE_Dataset(valid_subset.dataset.items(), valid_subset.indices.items())

          if custom:
              trainer = CustomTrainer(
                  loss_name='LDAMLoss',
                  model=model,  # the instantiated 🤗 Transformers model to be trained
                  args=training_args,  # training arguments, defined above
                  train_dataset=train_subset.dataset,  # training dataset
                  eval_dataset=valid_subset.dataset,  # evaluation dataset
                  compute_metrics=compute_metrics  # define metrics function
              )
          else:
              trainer = Trainer(
                  model=model,  # the instantiated 🤗 Transformers model to be trained
                  args=training_args,  # training arguments, defined above
                  train_dataset=train_subset,  # training dataset
                  eval_dataset=valid_subset,  # evaluation dataset
                  compute_metrics=compute_metrics  # define metrics function
              )
          # train model
          trainer.train()
          model.save_pretrained('./best_model/' + run_name + '_' + str(idx))

  else:
      trainer.train()
      model.save_pretrained('./best_model/' + run_name)

def main():
  MODE = "default"
  run_name = "bolim_LDAMloss_ep4"

  train(MODE=MODE, run_name=run_name)

if __name__ == '__main__':
    main()