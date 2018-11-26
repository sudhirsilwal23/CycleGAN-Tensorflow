from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import time
import multiprocessing
import glob

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
from models.losses import discriminator_loss, generator_loss, cycle_consistency_loss
from models.networks import Generator, Discriminator
from utils.image_history_buffer import ImageHistoryBuffer
tf.enable_eager_execution()

"""Define Hyperparameters"""
project_dir = os.path.abspath(os.path.join(os.getcwd(), os.pardir))
learning_rate = 0.0002 # TODO: DECAY!!!
batch_size = 1 # Set batch size to 4 or 16 if training multigpu
img_size = 256
cyc_lambda = 10
epochs = 20
trainA_path = os.path.join(project_dir, 'data', 'raw', 'horse2zebra', 'trainA')
trainB_path = os.path.join(project_dir, 'data', 'raw', 'horse2zebra', 'trainB')
trainA_size = len(os.listdir(trainA_path))
trainB_size = len(os.listdir(trainB_path))
batches_per_epoch = (trainA_size + trainB_size) // (2 * batch_size) # floor(Average dataset size / batch_size)

"""Load Datasets"""

def load_images(image_file):
    image = tf.read_file(image_file)
    image = tf.image.decode_jpeg(image, channels=3)
    image = tf.image.convert_image_dtype(image, tf.float32)
    image = tf.image.resize_images(image, [img_size, img_size])
    image = (image / 127.5) - 1 #Transform image to [-1, 1]
    return image

def download_data(download_location):
    path_to_zip = tf.keras.utils.get_file('horse2zebra.zip', cache_subdir=os.path.abspath(download_location),
        origin='https://people.eecs.berkeley.edu/~taesung_park/CycleGAN/datasets/horse2zebra.zip',
        extract=True)
    os.remove(path_to_zip)

def load_data(batch_size=batch_size, download=False):
    raw_data = os.path.join(project_dir, 'data', 'raw')
    if download:
        download_data(download_location=raw_data)

    path_to_dataset = os.path.join(raw_data, 'horse2zebra/')
    trainA_path = os.path.join(path_to_dataset, 'trainA')
    trainB_path = os.path.join(path_to_dataset, 'trainB')

    trainA_size = len(os.listdir(trainA_path))
    trainB_size = len(os.listdir(trainB_path))
    threads = multiprocessing.cpu_count()

    # Create Dataset from folder of string filenames.
    train_datasetA = tf.data.Dataset.list_files(trainA_path + os.sep + '*.jpg', shuffle=False)
    # Infinitely loop the dataset, shuffling once per epoch (in memory).
    # Safe to do when the dataset pipeline is currently string filenames.
    # Fused operation is faster than separated shuffle and repeat.
    # This is also serializable, so Dataset state can be saved with Checkpoints.
    train_datasetA = train_datasetA.apply(tf.contrib.data.shuffle_and_repeat(buffer_size=trainA_size))
    # Decodes filenames into jpegs, then stacks them into batches.
    # Throwing away the remainder allows the pipeline to report a fixed sized batch size,
    # aiding in model definition downstream.
    train_datasetA = train_datasetA.apply(tf.contrib.data.map_and_batch(lambda x: load_images(x),
                                                            batch_size=batch_size,
                                                            num_parallel_calls=threads,
                                                            drop_remainder=True))
    # Queue up a number of batches on CPU side
    train_datasetA = train_datasetA.prefetch(buffer_size=threads)
    # Queue up batches asynchronously onto the GPU.
    # As long as there is a pool of batches CPU side a GPU prefetch of 1 is fine.
    #train_datasetA = train_datasetA.apply(tf.contrib.data.prefetch_to_device("/gpu:0", buffer_size=1))

    train_datasetB = tf.data.Dataset.list_files(trainB_path + os.sep + '*.jpg', shuffle=False)
    train_datasetB = train_datasetB.apply(tf.contrib.data.shuffle_and_repeat(buffer_size=trainB_size))
    train_datasetB = train_datasetB.apply(tf.contrib.data.map_and_batch(lambda x: load_images(x),
                                                            batch_size=batch_size,
                                                            num_parallel_calls=threads,
                                                            drop_remainder=True))
    train_datasetB = train_datasetB.prefetch(buffer_size=threads)
    #train_datasetB = train_datasetB.apply(tf.contrib.data.prefetch_to_device("/gpu:0", buffer_size=1))

    return train_datasetA, train_datasetB

def generate_images(fake_A, fake_B):
    plt.figure(figsize=(15,15))
    fake_A = tf.reshape(fake_A, [256, 256, 3])
    fake_B = tf.reshape(fake_B, [256, 256, 3])
    display_list = [fake_A, fake_B]
    title = ["Generated A", "Generated B"]
    for i in range(2):
        plt.subplot(1, 2, i+1)
        plt.title(title[i])
        # getting the pixel values between [0, 1] to plot it.
        plt.imshow(display_list[i] * 0.5 + 0.5)
        plt.axis('off')
    plt.show()

def define_checkpoint(checkpoint_dir, model):
    nets, optimizers = model
    discA = nets['discA']
    discB = nets['discB']
    genA2B = nets['genA2B']
    genB2A = nets['genB2A']
    discA_opt = optimizers['discA_opt']
    discB_opt = optimizers['discB_opt']
    genA2B_opt = optimizers['genA2B_opt']
    genB2A_opt = optimizers['genB2A_opt']

    step_counter = tf.train.get_or_create_global_step()
    checkpoint = tf.train.Checkpoint(discA=discA, discB=discB, genA2B=genA2B, genB2A=genB2A,
                                 discA_opt=discA_opt, discB_opt=discB_opt, genA2B_opt=genA2B_opt,
                                 genB2A_opt=genB2A_opt, optimizer_step=step_counter)
    return checkpoint, checkpoint_dir

def restore_from_checkpoint(checkpoint, checkpoint_dir):
    latest_checkpoint = tf.train.latest_checkpoint(checkpoint_dir)
    if latest_checkpoint is not None:
        # Use assert_existing_objects_matched() instead of asset_consumed() here because
        # optimizers aren't initialized fully until first gradient update.
        # This will throw an exception if checkpoint does not restore the model weights.
        restore_obj = checkpoint.restore(latest_checkpoint).assert_existing_objects_matched()
        print("Checkpoint restored from ", latest_checkpoint)
        # Uncomment below to print full list of checkpoint metadata.
        #print(tf.contrib.checkpoint.object_metadata(latest_checkpoint))
    else:
        print("No checkpoint found, initializing model.")

def define_model(learning_rate, training=True):
    if not training:
        genA2B = Generator(img_size=img_size)
        genB2A = Generator(img_size=img_size)
        return {'genA2B':genA2B, 'genB2A':genB2A}
    else:
        discA = Discriminator()
        discB = Discriminator()
        genA2B = Generator(img_size=img_size)
        genB2A = Generator(img_size=img_size)
        discA_opt = tf.train.AdamOptimizer(learning_rate, beta1=0.5)
        discB_opt = tf.train.AdamOptimizer(learning_rate, beta1=0.5)
        genA2B_opt = tf.train.AdamOptimizer(learning_rate, beta1=0.5)
        genB2A_opt = tf.train.AdamOptimizer(learning_rate, beta1=0.5)

        nets = {'discA':discA, 'discB':discB, 'genA2B':genA2B, 'genB2A':genB2A}
        optimizers = {'discA_opt':discA_opt, 'discB_opt':discB_opt, 'genA2B_opt':genA2B_opt, 'genB2A_opt':genB2A_opt}
        return nets, optimizers

def test(data, model, checkpoint_info):
    genA2B = model['genA2B']
    genB2A = model['genB2A']

    checkpoint, checkpoint_dir = checkpoint_info
    restore_from_checkpoint(checkpoint, checkpoint_dir)
    test_datasetA, test_datasetB = iter(data[0]), iter(data[1])

    for test_step in range(batches_per_epoch):
        start = time.time()
        try:
            # Get next testing minibatches
            testA = next(test_datasetA)
            testB = next(test_datasetB)
        except tf.errors.OutOfRangeError:
            print("Error, run out of data")
            break

        genA2B_output = genA2B(testA, training=False)
        genB2A_output = genB2A(testB, training=False)
        generate_images(genB2A_output, genA2B_output)


def train(data, model, checkpoint_info, epochs, learning_rate=learning_rate, use_lsgan=True):
    nets, optimizers = model
    discA = nets['discA']
    discB = nets['discB']
    genA2B = nets['genA2B']
    genB2A = nets['genB2A']
    discA_opt = optimizers['discA_opt']
    discB_opt = optimizers['discB_opt']
    genA2B_opt = optimizers['genA2B_opt']
    genB2A_opt = optimizers['genB2A_opt']

    checkpoint, checkpoint_dir = checkpoint_info
    checkpoint_prefix = os.path.join(checkpoint_dir, 'ckpt')
    restore_from_checkpoint(checkpoint, checkpoint_dir)

    # Create a tf.data.Iterator from the Datasets:
    train_datasetA, train_datasetB = iter(data[0]), iter(data[1])
    discA_buffer = ImageHistoryBuffer(50, batch_size, img_size//8) # // 8 for PatchGAN
    discB_buffer = ImageHistoryBuffer(50, batch_size, img_size//8)
    global_step = tf.train.get_or_create_global_step()

    for epoch in range(epochs):
        start = time.time()
        for train_step in range(batches_per_epoch):
            try:
                # Get next training batches
                trainA = next(train_datasetA)
                trainB = next(train_datasetB)
            except tf.errors.OutOfRangeError:
                print("Error, run out of data")
                break
            with tf.GradientTape(persistent=True) as tape:

                genA2B_output = genA2B(trainA, training=True)
                genB2A_output = genB2A(trainB, training=True)

                discA_real_output = discA(trainA, training=True)
                discB_real_output = discB(trainB, training=True)

                discA_fake_output = discA(genB2A_output, training=True)
                discB_fake_output = discB(genA2B_output, training=True)
                # Sample from history buffer of 50 images:
                discA_fake_output = discA_buffer.query(discA_fake_output)
                discB_fake_output = discB_buffer.query(discB_fake_output)

                reconstructedA = genB2A(genA2B_output, training=True)
                reconstructedB = genA2B(genB2A_output, training=True)

                discA_loss = discriminator_loss(discA_real_output, discA_fake_output)
                discB_loss = discriminator_loss(discB_real_output, discB_fake_output)
                genA2B_loss = generator_loss(discB_fake_output) + \
                              cycle_consistency_loss(trainA, trainB, reconstructedA, reconstructedB)
                genB2A_loss = generator_loss(discA_fake_output) + \
                              cycle_consistency_loss(trainA, trainB, reconstructedA, reconstructedB)

            discA_gradients = tape.gradient(discA_loss, discA.variables)
            discB_gradients = tape.gradient(discB_loss, discB.variables)
            genA2B_gradients = tape.gradient(genA2B_loss, genA2B.variables)
            genB2A_gradients = tape.gradient(genB2A_loss, genB2A.variables)

            discA_opt.apply_gradients(zip(discA_gradients, discA.variables), global_step=global_step)
            discB_opt.apply_gradients(zip(discB_gradients, discB.variables), global_step=global_step)
            genA2B_opt.apply_gradients(zip(genA2B_gradients, genA2B.variables), global_step=global_step)
            genB2A_opt.apply_gradients(zip(genB2A_gradients, genB2A.variables), global_step=global_step)

            #if train_step % 100 == 0:
            print("Training step: ", train_step)
        # Checkpoint the model
        #if (epoch + 1) % 3 == 0:
            checkpoint_path = checkpoint.save(file_prefix=checkpoint_prefix)
            print("Checkpoint saved at ", checkpoint_path)
        print ("Time taken for epoch {} is {} sec\n".format(epoch + 1, time.time()-start))

if __name__ == "__main__":
    checkpoint_dir = os.path.join(project_dir, 'saved_models', 'checkpoints')
    testbuffer = ImageHistoryBuffer(50, batch_size, img_size)
    with tf.device("/cpu:0"): # Preprocess data on CPU for significant performance gains.
        data = load_data(batch_size=batch_size)
    #with tf.device("/gpu:0"):
        model = define_model(learning_rate=learning_rate)
        checkpoint_info = define_checkpoint(checkpoint_dir, model, data)
        train(data, model, checkpoint_info, epochs=epochs, learning_rate=learning_rate)
