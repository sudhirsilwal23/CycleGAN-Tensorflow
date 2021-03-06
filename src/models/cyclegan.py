import os

import tensorflow as tf

from models.losses import generator_loss, discriminator_loss, cycle_loss, identity_loss
from models.networks import Generator, Discriminator
from utils.image_history_buffer import ImageHistoryBuffer

class CycleGANModel(object):
    """
    CycleGAN model class, responsible for checkpointing and the forward and backward pass.
    Inspired by:
    https://github.com/junyanz/pytorch-CycleGAN-and-pix2pix/blob/master/models/cycle_gan_model.py
    """
    def __init__(self, opt):
        self.opt = opt

        self.genA2B = Generator(opt)
        self.genB2A = Generator(opt)

        if opt.training:
            self.discA = Discriminator(opt)
            self.discB = Discriminator(opt)
            self.learning_rate = tf.contrib.eager.Variable(opt.lr, dtype=tf.float32, name='learning_rate')
            self.disc_optim = tf.train.AdamOptimizer(self.learning_rate, beta1=opt.beta1)
            self.gen_optim = tf.train.AdamOptimizer(self.learning_rate, beta1=opt.beta1)
            self.global_step = tf.train.get_or_create_global_step()
            # Initialize history buffers:
            self.discA_buffer = ImageHistoryBuffer(opt)
            self.discB_buffer = ImageHistoryBuffer(opt)
        # Restore latest checkpoint:
        self.initialize_checkpoint()
        if not opt.training or opt.load_checkpoint:
            self.restore_checkpoint()

    def initialize_checkpoint(self):
        if self.opt.training:
            self.checkpoint = tf.train.Checkpoint(discA=self.discA,
                                                  discB=self.discB,
                                                  genA2B=self.genA2B,
                                                  genB2A=self.genB2A,
                                                  disc_optim=self.disc_optim,
                                                  gen_optim=self.gen_optim,
                                                  learning_rate=self.learning_rate,
                                                  global_step=self.global_step)
        else:
            self.checkpoint = tf.train.Checkpoint(genA2B=self.genA2B,
                                                  genB2A=self.genB2A)

    def restore_checkpoint(self):
        checkpoint_dir = os.path.join(self.opt.save_dir, 'checkpoints')
        latest_checkpoint = tf.train.latest_checkpoint(checkpoint_dir)
        if (not self.opt.training or self.opt.load_checkpoint) and latest_checkpoint is not None:
            # Use assert_existing_objects_matched() instead of asset_consumed() here because
            # optimizers aren't initialized fully until first gradient update.
            # This will throw an exception if the checkpoint does not restore the model weights.
            self.checkpoint.restore(latest_checkpoint).assert_existing_objects_matched()
            print("Checkpoint restored from ", latest_checkpoint)
        else:
            print("Failed to restore checkpoint, initializing model.")

    def set_input(self, input):
        # Get next batches:
        self.dataA = input["A"].get_next()
        self.dataB = input["B"].get_next()

    def forward(self):
        # Gen output shape: (batch_size, img_size, img_size, 3)
        self.fakeB = self.genA2B(self.dataA)
        self.reconstructedA = self.genB2A(self.fakeB)

        self.fakeA = self.genB2A(self.dataB)
        self.reconstructedB = self.genA2B(self.fakeA)

    def backward_D(self, netD, real, fake, tape):
        # Disc output shape: (batch_size, img_size/8, img_size/8, 1)
        pred_real = netD(real)
        pred_fake = netD(tf.stop_gradient(fake)) # Detaches generator from D
        disc_loss = discriminator_loss(pred_real, pred_fake, self.opt.gan_mode)
        if self.opt.gan_mode == 'wgangp': # GRADIENT PENALTY
            with tape.stop_recording():
                epsilon = tf.random_uniform(shape=[BATCH_SIZE, 1, 1, 1], minval=0., maxval=1.)
                X_hat = real + epsilon * (fake - real)
                def gp_func(X_hat):
                    return netD(X_hat)
                gp_grad_func = tf.contrib.eager.gradients_function(gp_func)
                grad_critic_X_hat = gp_grad_func(X_hat)[0]
            slopes = tf.sqrt(tf.reduce_sum(tf.square(grad_critic_X_hat), axis=[1, 2, 3]))
            gradient_penalty = tf.reduce_mean((slopes - 1.) ** 2)
            disc_loss += 10 * gradient_penalty # Lambda = 10 in gradient penalty
        return disc_loss

    def backward_discA(self, tape):
        # Sample from history buffer of 50 images:
        fake_A = self.discA_buffer.query(self.fakeA)
        self.discA_loss = self.backward_D(self.discA, self.dataA, fake_A, tape)
        return self.discA_loss

    def backward_discB(self, tape):
        # Sample from history buffer of 50 images:
        fake_B = self.discB_buffer.query(self.fakeB)
        self.discB_loss = self.backward_D(self.discB, self.dataB, fake_B, tape)
        return self.discB_loss

    def backward_G(self):
        if self.opt.identity_lambda > 0:
            identityA = self.genB2A(self.dataA)
            self.id_lossA = identity_loss(self.dataA, identityA) * self.opt.cyc_lambda * self.opt.identity_lambda

            identityB = self.genA2B(self.dataB)
            self.id_lossB = identity_loss(self.dataB, identityB) * self.opt.cyc_lambda * self.opt.identity_lambda
        else:
            id_lossA, id_lossB = 0, 0

        self.genA2B_loss = generator_loss(self.discB(self.dataB), self.discB(self.fakeB), self.opt.gan_mode)
        self.genB2A_loss = generator_loss(self.discA(self.dataA), self.discA(self.fakeA), self.opt.gan_mode)

        self.cyc_lossA = cycle_loss(self.dataA, self.reconstructedA) * self.opt.cyc_lambda
        self.cyc_lossB = cycle_loss(self.dataB, self.reconstructedB) * self.opt.cyc_lambda

        gen_loss = self.genA2B_loss + self.genB2A_loss + self.cyc_lossA + self.cyc_lossB + self.id_lossA + self.id_lossB
        return gen_loss

    def optimize_parameters(self):
        for net in (self.discA, self.discB):
            for layer in net.layers:
                layer.trainable = False

        with tf.GradientTape() as genTape:
            genTape.watch([self.genA2B.variables, self.genB2A.variables])

            self.forward()
            gen_loss = self.backward_G()

        gen_variables = [self.genA2B.variables, self.genB2A.variables]
        gen_gradients = genTape.gradient(gen_loss, gen_variables)
        self.gen_optim.apply_gradients(list(zip(gen_gradients[0], gen_variables[0])) \
                                     + list(zip(gen_gradients[1], gen_variables[1])),
                                     global_step=self.global_step)

        for net in (self.discA, self.discB):
            for layer in net.layers:
                layer.trainable = True

        with tf.GradientTape(persistent=True) as discTape:
            discTape.watch([self.discA.variables, self.discB.variables])
            self.forward()
            discA_loss = self.backward_discA(discTape)
            discB_loss = self.backward_discB(discTape)

        discA_gradients = discTape.gradient(discA_loss, self.discA.variables)
        discB_gradients = discTape.gradient(discB_loss, self.discB.variables)
        self.disc_optim.apply_gradients(zip(discA_gradients, self.discA.variables),
                                      global_step=self.global_step)
        self.disc_optim.apply_gradients(zip(discB_gradients, self.discB.variables),
                                      global_step=self.global_step)

    def save_model(self):
        checkpoint_prefix = os.path.join(self.opt.save_dir, 'checkpoints', 'ckpt')
        checkpoint_path = self.checkpoint.save(file_prefix=checkpoint_prefix)
        print("Checkpoint saved at ", checkpoint_path)

    def test(self):
        self.fakeA = self.genB2A(self.dataB)
        self.fakeB = self.genA2B(self.dataA)
        return [self.dataA, self.fakeA, self.dataB, self.fakeB]

    def update_learning_rate(self, batches_per_epoch):
        new_lr = self._get_learning_rate(batches_per_epoch)
        self.learning_rate.assign(new_lr)

    def _get_learning_rate(self, batches_per_epoch):
        global_step = self.global_step.numpy() / 3 # /3 because there are 3 gradient updates per batch.
        total_epochs = global_step // batches_per_epoch
        learning_rate_lambda = 1.0 - max(0, total_epochs - self.opt.niter) / float(self.opt.niter_decay + 1)
        return self.opt.lr * max(0, learning_rate_lambda)
