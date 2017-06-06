from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random
import inspect
import numpy as np
import tensorflow as tf
import copy
import logging
import preprocess_data
import tensorflow as tf
import numpy as np


class ECMModel(object):
    def __init__(self, embeddings, id2word, config, forward_only=False):
        magic_number = 256
        assert  (magic_number%2 == 0)
        self.embeddings = tf.cast(embeddings, dtype=tf.float32)
        # self.vocab_label = vocab_label  # label for vocab
        # self.emotion_label = emotion_label  # label for emotion
        self.config = config
        self.batch_size = config.batch_size
        #print("batch size", self.batch_size)
        self.vocab_size = config.vocab_size
        self.non_emotion_size = config.non_emotion_size
        #self.emotion_size = self.vocab_size - self.non_emotion_size
        self.id2word = id2word
        self.forward_only = forward_only
        self.emotion_kind = 6
        self.emotion_vector_dim = 100
        self.emotion_vector = tf.get_variable("emotion_vector", shape=[self.emotion_kind, self.emotion_vector_dim],
                                              initializer=tf.contrib.layers.xavier_initializer())


        '''if (self.config.vocab_size % 2 == 1):
            self.decoder_state_size = config.vocab_size + 1
            print (len(self.id2word))
            id2word.append('NULL')
        else:
            self.decoder_state_size = config.vocab_size'''

        self.decoder_state_size = magic_number
        self.encoder_state_size = int(self.decoder_state_size / 2)
        #input_size = self.batch_size, self.decoder_state_size * 2 + config.embedding_size
        input_size = [self.batch_size, self.decoder_state_size + self.emotion_vector_dim + config.embedding_size]
        self.pad_step_embedded = tf.zeros(input_size)
        self.go_step_embedded = tf.ones(input_size)

        self.GO_id = 1
        self.pad_id = 0
        self.IM_size = 256
        self.eps = 1e-5

        #with tf.variable_scope("reuse_sel_internalMemory") as scope:
        #scope.reuse_variables()
        self.internalMemory = tf.get_variable("IMFuck", shape=[self.emotion_kind, self.IM_size],
                                              initializer=tf.contrib.layers.xavier_initializer())

        self.vu = tf.get_variable("vu", shape=[self.decoder_state_size, 1], initializer=tf.contrib.layers.xavier_initializer())


        # self.sess = tf.Session(config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=True))

        self.question = tf.placeholder(tf.int32, shape=[None, None], name='question')
        self.question_len = tf.placeholder(tf.int32, shape=[None], name='question_len')
        if not self.forward_only:
            self.answer = tf.placeholder(tf.int32, shape=[None, None], name='answer')
            self.answer_len = tf.placeholder(tf.int32, shape=[None], name='answer_len')
            self.LA = tf.placeholder(dtype=tf.int32, name='LA', shape=())  # batch
        self.emotion_tag = tf.placeholder(tf.int32, shape=[None], name='emotion_tag')
        self.dropout_placeholder = tf.placeholder(dtype=tf.float32, name="dropout", shape=())
        self.LQ = tf.placeholder(dtype=tf.int32, name='LQ', shape=())  # batch

        with tf.variable_scope("ecm", initializer=tf.contrib.layers.xavier_initializer()):
            self.setup_embeddings()
            self.setup_system()
        self.merged_all = tf.summary.merge_all()

    def setup_embeddings(self):
        with tf.variable_scope("embeddings"):
            if self.config.retrain_embeddings:  # whether to cotrain word embedding
                embeddings = tf.Variable(self.embeddings, name="Emb", dtype=tf.float32)
            else:
                embeddings = tf.cast(self.embeddings, tf.float32)

            question_embeddings = tf.nn.embedding_lookup(embeddings, self.question)
            self.q = tf.reshape(question_embeddings, shape=[-1, self.LQ, self.config.embedding_size])
            answer_embeddings = tf.nn.embedding_lookup(embeddings, self.answer)
            self.a = tf.reshape(answer_embeddings, shape=[-1, self.LA, self.config.embedding_size])
        

    def encode(self, inputs, sequence_length, encoder_state_input, dropout=1.0):
        """
        In a generalized encode function, you pass in your inputs,
        sequence_length, and an initial hidden state input into this function.

        :param inputs: Symbolic representations of your input (padded all to the same length)
        :param mask: mask of the sequence
        :param encoder_state_input: (Optional) pass this as initial hidden state
                                    to tf.nn.dynamic_rnn to build conditional representations
        :return: an encoded representation of your input.
                 It can be context-level representation, word-level representation,
                 or both.
        """

        logging.debug('-' * 5 + 'encode' + '-' * 5)
        # Forward direction cell
        lstm_fw_cell = tf.contrib.rnn.LSTMCell(self.encoder_state_size, state_is_tuple=True)
        # Backward direction cell
        lstm_bw_cell = tf.contrib.rnn.LSTMCell(self.encoder_state_size, state_is_tuple=True)

        lstm_fw_cell = tf.contrib.rnn.DropoutWrapper(lstm_fw_cell, input_keep_prob=dropout)
        lstm_bw_cell = tf.contrib.rnn.DropoutWrapper(lstm_bw_cell, input_keep_prob=dropout)

        initial_state_fw = None
        initial_state_bw = None
        if encoder_state_input is not None:
            initial_state_fw, initial_state_bw = encoder_state_input
        logging.debug('sequence_length: %s' % str(sequence_length))
        logging.debug('Inputs: %s' % str(inputs))
        # Get lstm cell output
        print(inputs.get_shape())
        (outputs_fw, outputs_bw), (final_state_fw, final_state_bw) = tf.nn.bidirectional_dynamic_rnn(
            cell_fw=lstm_fw_cell,
            cell_bw=lstm_bw_cell,
            inputs=inputs,
            sequence_length=sequence_length,
            initial_state_fw=initial_state_fw,
            initial_state_bw=initial_state_bw,
            dtype=tf.float32)

        # Concatinate forward and backword hidden output vectors.
        # each vector is of size [batch_size, sequence_length, encoder_state_size]

        logging.debug('fw hidden state: %s' % str(outputs_fw))
        hidden_state = tf.concat([outputs_fw, outputs_bw], 2)
        logging.debug('Concatenated bi-LSTM hidden state: %s' % str(hidden_state))
        # final_state_fw and final_state_bw are the final states of the forwards/backwards LSTM
        print("encode output ", final_state_fw[1].get_shape())
        concat_final_state = tf.concat([final_state_fw[1], final_state_bw[1]], 1)
        logging.debug('Concatenated bi-LSTM final hidden state: %s' % str(concat_final_state))
        return hidden_state, concat_final_state

    def decode(self, encoder_outputs, encoder_final_state, decoder_length):
        print('decode start')

        # initialize first decode state
        def loop_fn_initial():
            initial_elements_finished = (0 >= decoder_length)  # all False at the initial step
            #GO_emb = tf.ones([self.batch_size], dtype=tf.int32, name='GO')
            initial_input = self.go_step_embedded#tf.nn.embedding_lookup(self.embeddings, GO_emb)
            initial_cell_state = encoder_final_state
            initial_cell_output = None
            initial_loop_state = None#self.internalMemory  # we don't need to pass any additional information
            print('before return initial')
            logging.debug('initial_elements_finished: %s' % str(initial_elements_finished))
            logging.debug('initial_input: %s' % str(initial_input))
            logging.debug('initial_cell_state: %s' % str(initial_cell_state))
            logging.debug('initial_cell_output: %s' % str(initial_cell_output))
            logging.debug('initial_loop_state: %s' % str(initial_loop_state))

            return (initial_elements_finished,
                    initial_input,
                    initial_cell_state,
                    initial_cell_output,
                    initial_loop_state)

        def loop_fn_transition(time, previous_output, previous_state, previous_loop_state):
            # get next state
            print('in trans')
            def get_next_input():
                print('in get next input')

                '''write_gate = tf.sigmoid(tf.layers.dense(previous_state, self.IM_size, name="write_gate"))
                eps_matrix = self.eps * tf.ones_like(write_gate)
                eps_write_gate = tf.log(eps_matrix + write_gate)
                write_one_hot = tf.one_hot(indices=self.emotion_tag, depth=self.emotion_kind)
                write_one_hot_transpose = tf.transpose(write_one_hot)

                tmpFuck = tf.sign(tf.reshape(tf.reduce_sum(write_one_hot_transpose,axis=1),[self.emotion_kind,1]))
                logging.debug('Before: %s' % str(tmpFuck))
                new_internalMemory = previous_loop_state * (1- tmpFuck)
                logging.debug('new_internalMemory: %s' % str(new_internalMemory))
                tmpFuck2 = tf.matmul(write_one_hot_transpose, eps_write_gate)
                logging.debug('TmpFuck2: %s' % str(tmpFuck2))
                new_internalMemory += tf.exp(tmpFuck)
                logging.debug('new_internalMemory: %s' % str(new_internalMemory))
                assert new_internalMemory.get_shape().as_list() == previous_loop_state.get_shape().as_list()

                #previous_loop_state = new_internalMemory

                previous_loop_state = new_internalMemory
                logging.debug('after: %s' % "fuck")'''


                tmp_id, _   = self.external_memory_function(previous_output)
                previous_output_id = tmp_id#tf.reshape(self.external_memory_function(previous_output), [self.batch_size])
                previous_output_vector = tf.nn.embedding_lookup(self.embeddings, previous_output_id)
                score = attention_mechanism(previous_state)
                weights = tf.nn.softmax(score)
                print("here")
                weights = tf.reshape(weights, [tf.shape(weights)[0], 1, tf.shape(weights)[1]])
                logging.debug('weights: %s' % str(weights))
                logging.debug('attention_mechanism.values: %s' % str(attention_mechanism.values))
                context = tf.matmul(weights, attention_mechanism.values)
                logging.debug('context: %s' % str(context))
                context = tf.reshape(context, [-1, context.get_shape().as_list()[2]])
                print("here1")
                logging.debug('previous_output_vector: %s' % str(previous_output_vector))
                logging.debug('context: %s' % str(context))
                attention = tf.layers.dense(inputs=tf.concat([previous_output_vector, context], 1), units=self.IM_size)
                '''read_gate = tf.sigmoid(attention, name="read_gate")
                logging.debug('read_gate: %s' % str(read_gate))
                read_gate_output = tf.nn.embedding_lookup(self.internalMemory,self.emotion_tag)
                logging.debug('gate output: %s' % str(read_gate_output))'''
                user_emotion_vector = tf.nn.embedding_lookup(self.emotion_vector, self.emotion_tag)
                logging.debug('user_emotion_vector: %s' % str(user_emotion_vector))
                next_input = tf.concat(
                    [context, previous_output_vector, user_emotion_vector], 1)#read_gate_output], 1)
                logging.debug('next_input: %s' % str(next_input))
                return next_input

            elements_finished = (time >= decoder_length)  # this operation produces boolean tensor of [batch_size]
            # defining if corresponding sequence has ended
            finished = tf.reduce_all(elements_finished)  # -> boolean scalar

            #pad_step_embedded = tf.nn.embedding_lookup(self.embeddings, self.pad_id)  ## undefined
            pad_step_embedded = self.pad_step_embedded
            logging.debug('finished: %s' % str(finished))
            logging.debug('pad_step_embedded: %s' % str(pad_step_embedded))


            '''if previous_state is not None:

                write_gate = tf.sigmoid(tf.layers.dense(previous_state, self.IM_size, name="write_gate"))
                eps_matrix = self.eps * tf.ones_like(write_gate)
                eps_write_gate = tf.log(eps_matrix + write_gate)
                write_one_hot = tf.one_hot(indices=self.emotion_tag, depth=self.emotion_kind)
                write_one_hot_transpose = tf.transpose(write_one_hot)

                tmpFuck = tf.sign(tf.reshape(tf.reduce_sum(write_one_hot_transpose,axis=1),[self.emotion_kind,1]))
                logging.debug('Before: %s' % str(tmpFuck))
                new_internalMemory = previous_loop_state * (1- tmpFuck)
                logging.debug('new_internalMemory: %s' % str(new_internalMemory))
                tmpFuck2 = tf.matmul(write_one_hot_transpose, eps_write_gate)
                logging.debug('TmpFuck2: %s' % str(tmpFuck2))
                new_internalMemory += tf.exp(tmpFuck)
                logging.debug('new_internalMemory: %s' % str(new_internalMemory))
                assert new_internalMemory.get_shape().as_list() == previous_loop_state.get_shape().as_list()
                previous_loop_state = new_internalMemory
                logging.debug('after: %s' % "fuck")'''


            inputNow = tf.cond(finished, lambda : pad_step_embedded , get_next_input)
            #loop_state =  tf.cond(finished, None, previous_loop_state)
            logging.debug('inputNow: %s' % str(inputNow))
            logging.debug('previous_state: %s' % str(previous_state))
            loop_state = previous_loop_state
            output = previous_output
            state = previous_state
            #output, state = decode_cell(inputNow, previous_state)


            #write_gate = tf.sigmoid(tf.layers.dense(state, self.IM_size, name="write_gate"))
            #change_IM = tf.nn.embedding_lookup(self.internalMemory,self.emotion_tag)
            #change_IM = change_IM * write_gate

            return (elements_finished,
                    inputNow,
                    state,
                    output,
                    loop_state)

        def loop_fn(time, previous_output, previous_state, previous_loop_state):
            if previous_state is None:  # time == 0
                assert previous_output is None and previous_state is None
                print("initialii******")
                return loop_fn_initial()
            else:
                print("trainsition******")
                return loop_fn_transition(time, previous_output, previous_state, previous_loop_state)

        decode_cell = tf.contrib.rnn.GRUCell(self.decoder_state_size)
        attention_mechanism = tf.contrib.seq2seq.LuongAttention(self.decoder_state_size, encoder_outputs)
        decoder_outputs_ta, decoder_final_state, _ = tf.nn.raw_rnn(decode_cell, loop_fn)
        decoder_outputs = decoder_outputs_ta.stack()
        decoder_max_steps, decoder_batch_size, decoder_dim = tf.unstack(tf.shape(decoder_outputs))#decoder_outputs.get_shape().as_list()#tf.unstack(tf.shape(decoder_outputs))
        #assert (decoder_batch_size.as_list()[0] == self.batch_size)
        #assert (decoder_dim.as_list()[0] == self.decoder_state_size)
        decoder_outputs_reshape = tf.reshape(decoder_outputs, [decoder_batch_size,decoder_max_steps , decoder_dim])
        return decoder_outputs_reshape, decoder_final_state

    def external_memory_function(self, decode_state):  # decode_output, shape[batch_size,decode_size]
        print('flag1')
        #decode_output = tf.reshape(in_decode_output, [self.batch_size,-1,self.decoder_state_size])
        #gto = tf.sigmoid(tf.reduce_sum(tf.matmul(decode_state, self.vu),axis= 1))
        #gto = tf.reshape(gto, [tf.shape(gto)[0],1])
        gto = tf.sigmoid(tf.matmul(decode_state, self.vu))
        logging.debug('gto: %s' % str(gto))
        print('flag2')
        #emotion_num = self.emotion_size
        decode_output = tf.layers.dense(decode_state, self.vocab_size, name="state2output")
        print('flag3')
        arg = tf.argmax(tf.concat([ (1-gto) * decode_output[:,:self.non_emotion_size], gto * decode_output[:, self.non_emotion_size:]],
                                   1), axis=1)  # [batch_size * seq,1]
        logging.debug('arg: %s' % str(arg))
        return arg, decode_output


    def create_feed_dict(self, question_batch, question_len_batch, emotion_tag_batch, answer_batch=None,
                         answer_len_batch=None, is_train=True):
        feed_dict = {}
        LQ = np.max(question_len_batch)

        def add_paddings(sentence, max_length):
            pad_len = max_length - len(sentence)
            if pad_len > 0:
                padded_sentence = sentence + [0] * pad_len
            else:
                padded_sentence = sentence[:max_length]
            return padded_sentence

        def padding_batch(data, max_len):
            padded_data = []
            for sentence in data:
                d = add_paddings(sentence, max_len)
                padded_data.append(d)
            return padded_data

        feed_dict[self.question_len] = question_len_batch
        feed_dict[self.LQ] = LQ
        feed_dict[self.emotion_tag] = emotion_tag_batch
        padded_question = padding_batch(question_batch, LQ)
        print("padding question size ", np.array(padded_question).shape)
        feed_dict[self.question] = padded_question

        if not self.forward_only:
            assert answer_batch is not None
            assert answer_len_batch is not None
            LA = np.max(answer_len_batch)
            padded_answer = padding_batch(answer_batch, LA)
            feed_dict[self.answer] = padded_answer
            feed_dict[self.answer_len] = answer_len_batch
            feed_dict[self.LA] = LA

        if is_train:
            feed_dict[self.dropout_placeholder] = 0.8
        else:
            feed_dict[self.dropout_placeholder] = 1.0

        return feed_dict

    def setup_system(self):
        def emotion_distribution(decode_outputs_ids):

            return tf.cast((decode_outputs_ids < (self.non_emotion_size)), dtype=tf.int64)

        def loss(results, final_IM):
            #logging.debug('logits: %s' % str(results))
            logging.debug('labels: %s' % str(self.answer))
            #answer_all = tf.reshape(self.a, [-1,self.config.embedding_size])

            answer_one_hot = tf.one_hot(indices= self.answer, depth= self.vocab_size, on_value= 1, off_value=0,axis=-1)#, dtype=tf.float32)
            answer_one_hot = tf.cast(answer_one_hot, dtype=tf.float32)
            answer_one_hot = tf.reshape(answer_one_hot,[-1, self.vocab_size])
            #results = tf.reshape(results, [-1,results.get_shape().as_list()[2]])
            #results = tf.cast(self.external_memory_function(results), dtype=tf.float32)
            EM_ids, EM_output = self.external_memory_function(tf.reshape(results,[-1,self.decoder_state_size]))
            EM_ids = tf.reshape(EM_ids,[self.batch_size,-1])
            #EM_output = tf.reshape(EM_output,[self.batch_size,-1, self.vocab_size])
            logging.debug('logits: %s' % str(EM_output))
            logging.debug('labels: %s' % str(answer_one_hot))
            logging.debug('EM_ID: %s' % str(EM_ids))

            tmp = tf.nn.softmax_cross_entropy_with_logits(logits=EM_output, labels=answer_one_hot)
            logging.debug('tmp loss 1: %s' % str(tmp))
            loss = tf.reduce_sum(tmp) # self.vocab_label)
            print("loss 1 ptint ", loss)
            emotion_label = tf.cast((self.answer < (self.non_emotion_size)), dtype=tf.float32)
            emotion_logit = tf.cast((EM_ids < (self.non_emotion_size)), dtype=tf.float32)

            logging.debug('emotion logits: %s' % str(emotion_logit))
            logging.debug('emotion labels: %s' % str(emotion_label))


            tmp = tf.nn.softmax_cross_entropy_with_logits(logits=tf.cast(emotion_logit, dtype=tf.float32),
                                                            labels=tf.cast(emotion_label, dtype=tf.float32))
            logging.debug('tmp loss 2: %s' % str(tmp))
            loss += tf.reduce_sum(tmp)
            print("loss 2 ptint ", loss)
            loss += 2 * tf.nn.l2_loss(final_IM)
            print("loss 3 ptint ", loss)
            logging.debug('loss: %s' % str(loss))
            EM_output = tf.reshape(EM_output,[self.batch_size,-1, self.vocab_size])
            return loss, EM_output

        encoder_outputs, encoder_final_state = self.encode(self.q, self.question_len, None, self.dropout_placeholder)
        results, final_IM = self.decode(encoder_outputs, encoder_final_state, self.answer_len)
        if not self.forward_only:
            logging.debug('results: %s' % str(results))
            self.tfloss, self.EM_output = loss(results, final_IM)
            loss_sum = tf.summary.scalar("loss", self.tfloss)
            self.train_op = tf.train.AdamOptimizer(self.config.learning_rate, beta1=0.5).minimize(self.tfloss)
        else:
            EM_ids, EM_output = self.external_memory_function(tf.reshape(results,[-1,self.decoder_state_size]))
            self.EM_output = tf.reshape(EM_output,[self.batch_size,-1, self.vocab_size])

        self.tfids = tf.argmax(self.EM_output, axis=2)
        logging.debug('self.tfids: %s' % str(self.tfids))

    def train(self, sess, training_set, tensorboard=False):
        question_batch, question_len_batch, answer_batch, answer_len_batch, tag_batch = training_set
        tag_batch = map(lambda x: x[0],tag_batch)
        input_feed = self.create_feed_dict(question_batch, question_len_batch, tag_batch, answer_batch,
                                           answer_len_batch, is_train=True)
        if tensorboard:
            run_options = tf.RunOptions(trace_level=tf.RunOptions.FULL_TRACE)
            run_metadata = tf.RunMetadata()
            _, loss, merged = sess.run([self.train_op, self.tfloss, self.merged_all], feed_dict=input_feed, options=run_options, run_metadata=run_metadata)
            return loss, merged
        else:
            _, loss = sess.run([self.train_op, self.tfloss], feed_dict=input_feed)
            return loss

    def answer(self, sess, dataset):
        #print(len(dataset))
        assert self.forward_only == True
        question_batch, question_len_batch, _, _, tag_batch = dataset
        tag_batch = map(lambda x: x[0],tag_batch)
        answer_len_batch = 10 * np.ones(self.batch_size)

        input_feed = self.create_feed_dict(question_batch, question_len_batch, tag_batch, answer_batch=None,
                                           answer_len_batch=answer_len_batch, is_train=False)
        ids = sess.run([self.tfids], feed_dict=input_feed)
        return [[self.id2word[each] for each in each_list] for each_list in ids]

    def test(self, sess, test_set):
        #print(len(test_set))
        question_batch, question_len_batch, answer_batch, answer_len_batch, tag_batch = test_set
        tag_batch = map(lambda x: x[0],tag_batch)
        input_feed = self.create_feed_dict(question_batch, question_len_batch, tag_batch, answer_batch,
                                           answer_len_batch, is_train=False)
        loss, ids = sess.run([self.tfloss, self.tfids], feed_dict=input_feed)
        return loss, [[self.id2word[each] for each in each_list] for each_list in ids]
