# -*- coding: utf-8 -*-
#/usr/bin/python3
'''
date: 2019/5/21
mail: cally.maxiong@gmail.com
page: http://www.cnblogs.com/callyblog/
'''

import logging
import os

from sumeval.metrics.rouge import RougeCalculator
from tqdm import tqdm

from beam_search import BeamSearch
from data_load import get_batch, _load_vocab
from hparams import Hparams
from model import Transformer
from utils import save_hparams, save_variable_specs, get_hypotheses, calc_rouge, import_tf

# logging日志一共分成5个等级，从低到高分别是：DEBUG INFO WARNING ERROR CRITICAL
# logging与print 区别，为什么需要logging？
# 在写脚本的过程中，为了调试程序，我们往往会写很多print打印输出以便用于验证，验证正确后往往会注释掉，一旦验证的地方比较多，再一一注释比较麻烦，]
# 这样logging就应运而生了，直接把验证信息存在一个文件中（例如在logging.basicConfig(里面设置filename= ‘employee.log’,）or直接打印出出来，不用设置filname，就会直接打印在cmd窗口中。

logging.basicConfig(level=logging.INFO)

rouge = RougeCalculator(stopwords=True, lang="zh")

logging.info("# hparams")
hparams = Hparams()
parser = hparams.parser
hp = parser.parse_args()
# hp为： Namespace(batch_size=32, beam_size=4, d_ff=2048, d_model=512, dropout_rate=0.1, eval='data/eval.csv', eval_batch_size=32, eval_rouge='data/test.csv', evaldir='eval/1', gpu_nums=1, logdir='log/2', lr=0.0005, maxlen1=150, maxlen2=25, num_blocks=6, num_epochs=5, num_heads=8, stop_vocab='stop_vocab', train='data/train.csv', vocab='vocab', vocab_size=10598, warmup_steps=4000)

# import tensorflow
gpu_list = [str(i) for i in list(range(hp.gpu_nums))]
tf = import_tf(gpu_list)

# 保存超参数
save_hparams(hp, hp.logdir)

logging.info("# Prepare train/eval batches")
# 更改为train
train_batches, num_train_batches, num_train_samples = get_batch(hp.train,
                                                                hp.maxlen1,
                                                                hp.maxlen2,
                                                                hp.vocab,
                                                                hp.batch_size,
                                                                hp.gpu_nums,
                                                                shuffle=True)

eval_batches, num_eval_batches, num_eval_samples = get_batch(hp.eval,
                                                             hp.maxlen1,
                                                             hp.maxlen2,
                                                             hp.vocab,
                                                             hp.eval_batch_size,
                                                             hp.gpu_nums,
                                                             shuffle=False)

handle = tf.placeholder(tf.string, shape=[])
iter = tf.data.Iterator.from_string_handle(
    handle, train_batches.output_types, train_batches.output_shapes)

# create a iter of the correct shape and type
xs, ys = iter.get_next()

logging.info('# init data')
training_iter = train_batches.make_one_shot_iterator()
val_iter = eval_batches.make_initializable_iterator()

logging.info("# Load model")
m = Transformer(hp)

# get op
loss, train_op, global_step, train_summaries = m.train(xs, ys)
y_hat, eval_summaries = m.eval(xs, ys)

# 返回vocab字典的词及id, id及词
token2idx, idx2token = _load_vocab(hp.vocab)

bs = BeamSearch(m, hp.beam_size, list(idx2token.keys())[2], list(idx2token.keys())[3], idx2token, hp.maxlen2, m.x,
                m.decoder_inputs, m.logits)

logging.info("# Session")
saver = tf.train.Saver(max_to_keep=hp.num_epochs)
with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
    ckpt = tf.train.latest_checkpoint(hp.logdir)
    if ckpt is None:
        logging.info("Initializing from scratch")
        sess.run(tf.global_variables_initializer())
        save_variable_specs(os.path.join(hp.logdir, "specs"))
    else:
        saver.restore(sess, ckpt)

    summary_writer = tf.summary.FileWriter(hp.logdir, sess.graph)

    # Iterator.string_handle() get a tensor that can be got value to feed handle placeholder
    training_handle = sess.run(training_iter.string_handle())
    val_handle = sess.run(val_iter.string_handle())

    total_steps = hp.num_epochs * num_train_batches * 2
    _gs = sess.run(global_step)
    for i in tqdm(range(_gs, total_steps+1)):
        _, _gs, _summary = sess.run([train_op, global_step, train_summaries], feed_dict={handle: training_handle})
        summary_writer.add_summary(_summary, _gs)
        print(_gs)
        if _gs % (hp.gpu_nums * 10000) == 0 and _gs != 0:
            logging.info("steps {} is done".format(_gs))

            logging.info("# test evaluation")
            sess.run(val_iter.initializer) # initial val dataset
            _eval_summaries = sess.run(eval_summaries, feed_dict={handle: val_handle})
            summary_writer.add_summary(_eval_summaries, _gs)

            logging.info("# beam search")
            hypotheses, all_targets = get_hypotheses(num_eval_batches, num_eval_samples, sess, m, bs, [xs[0], ys[2]],
                                                     handle, val_handle)

            logging.info("# calc rouge score ")
            if not os.path.exists(hp.evaldir): os.makedirs(hp.evaldir)
            rouge_l = calc_rouge(rouge, all_targets, hypotheses, _gs, hp.evaldir)

            model_output = "trans_pointer%02dL%.2f" % (_gs, rouge_l)

            logging.info('# write hypotheses')
            with open(os.path.join(hp.evaldir, model_output), 'w', encoding='utf-8') as f:
                for target, hypothes in zip(all_targets, hypotheses):
                    f.write('{}-{} \n'.format(target, ' '.join(hypothes)))

            logging.info("# save models")

            ckpt_name = os.path.join(hp.logdir, model_output)
            saver.save(sess, ckpt_name, global_step=_gs)
            logging.info("after training of {} steps, {} has been saved.".format(_gs, ckpt_name))

            logging.info("# fall back to train mode")
    summary_writer.close()

logging.info("Done")