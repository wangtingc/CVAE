import os
import sys
import datetime
import tensorflow as tf
import numpy as np
import prettytensor as pt
from convolutional_vae_util import deconv2d

from utils import *

class CVAE(object):
    '''
    CVAE: Convolutional Variational AutoEncoder

    Builds a convolutional variational autoencoder that compresses
    input_shape to latent_size and then back out again. It uses
    the reparameterization trick and conv/conv transpose to achieve this.

    '''
    def __init__(self, sess, input_shape, batch_size, latent_size=128, e_dim=64, d_dim=64):
        self.input_shape = input_shape
        self.input_size = np.prod(input_shape)
        self.latent_size = latent_size
        self.batch_size = batch_size
        self.e_dim = e_dim
        self.d_dim = d_dim
        self.iteration = 0

        with tf.variable_scope(self.get_name()):
            self.inputs = tf.placeholder(tf.float32, [None, self.input_size], name="inputs")
            self.is_training = tf.placeholder(tf.bool, name="is_training")

            # Encode our data into z and return the mean and covariance
            self.z_mean, self.z_log_sigma_sq = self.encoder(self.inputs, latent_size)

            # z = mu + sigma * epsilon
            # epsilon is a sample from a N(0, 1) distribution
            eps = tf.random_normal([batch_size, latent_size], 0.0, 1.0, dtype=tf.float32)
            self.z = tf.add(self.z_mean,
                            tf.mul(self.z_log_sigma_sq, eps))
            self.z_summary = tf.histogram_summary("z", self.z)

            # Get the reconstructed mean from the decoder
            self.x_reconstr_mean = self.decoder(self.z, self.input_size)

            self.loss, self.optimizer = self._create_loss_and_optimizer(self.inputs,
                                                                        self.x_reconstr_mean,
                                                                        self.z_log_sigma_sq,
                                                                        self.z_mean)
            self.loss_summary = tf.scalar_summary("loss", self.loss)
            self.summaries = tf.merge_all_summaries()
            self.summary_writer = tf.train.SummaryWriter("logs/" + self.get_name() + self.get_formatted_datetime(),
                                                         sess.graph)
            self.saver = tf.train.Saver()

    def save(self, sess, filename):
        print 'saving cvae model to %s...' % filename
        self.saver.save(sess, filename)

    def load(self, sess, filename):
        if os.path.isfile(filename):
            print 'restoring cvae model from %s...' % filename
            self.saver.restore(sess, filename)

    def get_name(self):
        return "cvae_input_%dx%d_batch%d_latent%d_edim%d_ddim%d" % (self.input_shape[0],
                                                                    self.input_shape[1],
                                                                    self.batch_size,
                                                                    self.latent_size,
                                                                    self.e_dim,
                                                                    self.d_dim)
    def get_formatted_datetime(self):
        return str(datetime.datetime.now()).replace(" ", "_") \
                                           .replace("-", "_") \
                                           .replace(":", "_")

    # Taken from https://jmetzen.github.io/2015-11-27/vae.html
    def _create_loss_and_optimizer(self, inputs, x_reconstr_mean, z_log_sigma_sq, z_mean):
        # The loss is composed of two terms:
        # 1.) The reconstruction loss (the negative log probability
        #     of the input under the reconstructed Bernoulli distribution
        #     induced by the decoder in the data space).
        #     This can be interpreted as the number of "nats" required
        #     for reconstructing the input when the activation in latent
        #     is given.
        # Adding 1e-10 to avoid evaluation of log(0.0)
        self.reconstr_loss = \
            -tf.reduce_sum(inputs * tf.log(tf.clip_by_value(x_reconstr_mean, 1e-10, 1.0))
                           + (1.0 - inputs) * tf.log(tf.clip_by_value(1.0 - x_reconstr_mean, 1e-10, 1.0)),
                           1)
        # 2.) The latent loss, which is defined as the Kullback Libeler divergence
        ##    between the distribution in latent space induced by the encoder on
        #     the data and some prior. This acts as a kind of regularize.
        #     This can be interpreted as the number of "nats" required
        #     for transmitting the the latent space distribution given
        #     the prior.
        self.latent_loss = -0.5 * tf.reduce_sum(1.0 + z_log_sigma_sq
                                           - tf.square(z_mean)
                                           - tf.exp(z_log_sigma_sq), 1)
        loss = tf.reduce_mean(self.reconstr_loss + self.latent_loss)   # average over batch

        optimizer = tf.train.AdamOptimizer(learning_rate=1e-4).minimize(loss)
        return loss, optimizer


    def decoder(self, z, projection_size, activ=tf.nn.elu):
        with pt.defaults_scope(activation_fn=activ,
                               batch_normalize=True,
                               learned_moments_update_rate=0.0003,
                               variance_epsilon=0.001,
                               scale_after_normalization=True):
            return (pt.wrap(z).
                    reshape([-1, 1, 1, self.latent_size]).
                    deconv2d(3, 128, edges='VALID').
                    deconv2d(5, 64, edges='VALID').
                    deconv2d(5, 32, stride=2).
                    deconv2d(5, 1, stride=2, activation_fn=tf.nn.sigmoid).
                    flatten()).tensor

    def encoder(self, inputs, latent_size, activ=tf.nn.elu):
        with pt.defaults_scope(activation_fn=activ,
                           batch_normalize=True,
                           learned_moments_update_rate=0.0003,
                           variance_epsilon=0.001,
                           scale_after_normalization=True):
            params = (pt.wrap(inputs).
                      reshape([-1, self.input_shape[0], self.input_shape[1], 1]).
                      conv2d(5, 32, stride=2).
                      conv2d(5, 64, stride=2).
                      conv2d(5, 128, edges='VALID').
                      dropout(0.9).
                      flatten().
                      fully_connected(self.latent_size * 2, activation_fn=None)).tensor

        mean = params[:, :self.latent_size]
        stddev = tf.sqrt(tf.exp(params[:, self.latent_size:]))
        return [mean, stddev]

    def partial_fit(self, sess, X):
        """Train model based on mini-batch of input data.

        Return cost of mini-batch.
        """
        feed_dict = {self.inputs: X,
                     self.is_training: True}

        if self.iteration % 10 == 0:
            _, summary, cost  = sess.run([self.optimizer, self.summaries, self.loss],
                                         feed_dict=feed_dict)
            self.summary_writer.add_summary(summary, self.iteration)
        else:
            _, cost  = sess.run([self.optimizer, self.loss],
                                feed_dict=feed_dict)

        self.iteration += 1
        return cost

    def transform(self, sess, inputs):
        """
        Transform data by mapping it into the latent space.
        Taken from https://jmetzen.github.io/2015-11-27/vae.html
        """
        # Note: This maps to mean of distribution, we could alternatively
        # sample from Gaussian distribution
        feed_dict={self.inputs: inputs,
                   self.is_training: False}
        return sess.run(self.z_mean,
                        feed_dict=feed_dict)

    def generate(self, sess):
        """
        Generate data by sampling from latent space.
        Taken from https://jmetzen.github.io/2015-11-27/vae.html
        """
        # Note: This maps to mean of distribution, we could alternatively
        # sample from Gaussian distribution
        feed_dict={self.z: z_mu,
                   self.is_training: False}
        return sess.run(self.x_reconstr_mean,
                        feed_dict=feed_dict)

    def reconstruct(self, sess, X):
        """
        Use VAE to reconstruct given data.
        Taken from https://jmetzen.github.io/2015-11-27/vae.html
        """
        feed_dict={self.inputs: X,
                   self.is_training: False}
        return sess.run(self.x_reconstr_mean,
                        feed_dict=feed_dict)

    def train(self, sess, source, batch_size, training_epochs=10, display_step=5):
        n_samples = source.train.num_examples
        for epoch in range(training_epochs):
            avg_cost = 0.
            total_batch = int(n_samples / batch_size)
            # Loop over all batches
            for i in range(total_batch):
                batch_xs, _ = source.train.next_batch(batch_size)

                # Fit training using batch data
                cost = self.partial_fit(sess, batch_xs)
                # Compute average loss
                avg_cost += cost / n_samples * batch_size

            # Display logs per epoch step
            #if epoch % display_step == 0:
                print "[Epoch:", '%04d]' % (epoch+1), \
                    "current cost = ", "{:.9f} | ".format(cost), \
                    "avg cost = ", "{:.9f}".format(avg_cost)

    def init_all(self, sess):
        sess.run(tf.initialize_all_variables(), feed_dict={self.is_training: True})
        sess.run(tf.initialize_all_variables(), feed_dict={self.is_training: False})
