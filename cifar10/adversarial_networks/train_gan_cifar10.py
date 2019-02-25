import tensorflow as tf
import time

# construct graph for testing on original examples
def build_test(discriminator, test_data, config):
    x = test_data[0]
    x = 2.0*x - 1.0
    y = test_data[1]
    d_out = discriminator(x)
    predictions = tf.argmax(d_out, 1)

    # http://ronny.rest/blog/post_2017_09_11_tf_metrics/
    tf_metric, tf_metric_update = tf.metrics.accuracy(y, predictions, name='test_metric')
    running_vars = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="test_metric")
    running_vars_initializer = tf.variables_initializer(var_list=running_vars)

    return (tf_metric, tf_metric_update, running_vars_initializer)

# run actual test to collect performance statistics
def run_test(tf_metric, tf_metric_update, running_vars_initializer, sess, config):
    sess.run(running_vars_initializer)

    batch_size = config['batch_size']
    test_size = config['test_size']
    num_steps = int(test_size/batch_size)
    for i in range(num_steps):
        sess.run(tf_metric_update)

    accuracy = sess.run(tf_metric)

    return accuracy

# construct graph for testing on examples perturbed by G
def build_test_G(discriminator, generator, test_data, config):
    x = test_data[0]
    x = 2.0*x - 1.0
    y = test_data[1]
    epsilon = config['epsilon']

    x_noise = tf.stop_gradient(generator(x))
    x_perturbed = x + epsilon*x_noise

    d_out = discriminator(x_perturbed)
    predictions = tf.argmax(d_out, 1)

    tf_metric, tf_metric_update = tf.metrics.accuracy(y, predictions, name='g_metric')
    running_vars = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="g_metric")
    running_vars_initializer = tf.variables_initializer(var_list=running_vars)

    return (tf_metric, tf_metric_update, running_vars_initializer)

# construct graph for testing on examples perturbed with FGS
def build_test_fgs(discriminator, test_data, config):
    x = test_data[0]
    x = 2.0*x - 1.0
    y = test_data[1]
    epsilon = config['epsilon']
    class_num = config['class_num']

    d_out = discriminator(x)
    d_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
        logits=d_out, labels=tf.one_hot(y,class_num)))

    grad_x, = tf.gradients(d_loss, x)
    x_adv = tf.stop_gradient(x + epsilon*tf.sign(grad_x))
    x_adv = tf.clip_by_value(x_adv, -1.0, 1.0)

    adv_out = discriminator(x_adv)
    predictions = tf.argmax(adv_out, 1)

    tf_metric, tf_metric_update = tf.metrics.accuracy(y, predictions, name='fgs_metric')
    running_vars = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="fgs_metric")
    running_vars_initializer = tf.variables_initializer(var_list=running_vars)

    return (tf_metric, tf_metric_update, running_vars_initializer)


# construct graph for testing on examples perturbed with PGD
def build_test_pgd(discriminator, test_data, config):
    x0 = test_data[0]

    x0 = 2.0*x0 - 1.0
    y = test_data[1]
    epsilon = config['epsilon']
    class_num = config['class_num']
    pgd_iter = config['pgd_iter']

    step_size = epsilon*0.25

    # randomize
    x = x0 + tf.random_uniform(x0.shape, -epsilon, epsilon)
    for i in range(pgd_iter):
        d_out = discriminator(x)
        d_loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
            logits=d_out, labels=tf.one_hot(y,class_num)))

        grad_x, = tf.gradients(d_loss, x)
        x = tf.stop_gradient(x + step_size*tf.sign(grad_x))
        x = tf.clip_by_value(x, x0 - epsilon, x0 + epsilon)
        x = tf.clip_by_value(x, -1.0, 1.0)

    adv_out = discriminator(x)
    predictions = tf.argmax(adv_out, 1)

    tf_metric, tf_metric_update = tf.metrics.accuracy(y, predictions, name='pgd_metric')
    running_vars = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="pgd_metric")
    running_vars_initializer = tf.variables_initializer(var_list=running_vars)

    return (tf_metric, tf_metric_update, running_vars_initializer)



def train(generator, discriminator, data, test_data, config):
    tf.set_random_seed(int(config['random_seed']))

    batch_size = config['batch_size']
    epsilon = config['epsilon']    # size of perturbation
    class_num = config['class_num']    # number of output classes
    weight_decay = config['weight_decay']
    learning_rate = config['learning_rate']
    gamma = config['gamma']    # gradient regularization parameter   
    train_size = config['train_size']    # training set size

    print_iter = config['print_iter']
    save_iter = config['save_iter']


    x_real = data[0]
    label = data[1]

    # Data augmentation for CIFAR10
    x_real = tf.map_fn(lambda x: tf.image.resize_image_with_crop_or_pad(x, 32+4, 32+4), x_real)
    x_real = tf.map_fn(lambda x: tf.random_crop(x, [32,32,3]), x_real)
    x_real = tf.map_fn(lambda x: tf.image.random_flip_left_right(x), x_real)

    # Normalize to range [-1,1]
    x_real = 2.*x_real - 1.

    x_noise = generator(x_real)
    x_perturbed = x_real + epsilon*x_noise

    d_out_real = discriminator(x_real)
    d_out_noise = discriminator(x_perturbed)
    d_loss_real = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
        logits=d_out_real, labels=tf.one_hot(label,class_num)))
    d_loss_noise = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
        logits=d_out_noise, labels=tf.one_hot(label,class_num)))

    # Losses for the discriminator network and generator network
    d_loss = d_loss_real + d_loss_noise
    g_loss = -d_loss_noise

    # Weight decay: assume weights are named 'kernel' or 'weights' 
    g_vars =  tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='generator')
    d_vars =  tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='discriminator')

    d_decay = weight_decay * 0.5 * sum(
        tf.reduce_sum(tf.square(v)) for v in d_vars if (v.name.find('kernel')>0 or v.name.find('weights')>0)
    )
    g_decay = weight_decay * 0.5 * sum(
        tf.reduce_sum(tf.square(v)) for v in g_vars if (v.name.find('kernel')>0 or v.name.find('weights')>0)
    )


    # SGD optimizer with different step sizes
    d_optimizer = tf.train.MomentumOptimizer(learning_rate=learning_rate, momentum=0.9)
    d_optimizer2 = tf.train.MomentumOptimizer(learning_rate=learning_rate/10, momentum=0.9)
    g_optimizer = tf.train.AdamOptimizer(learning_rate=0.002, beta1=0.5, beta2=0.999)


    d_grads_noise = tf.gradients(d_loss_noise, d_vars)
    d_grads_real = tf.gradients(d_loss_real + d_decay, d_vars)

    d_grads = [(g1+g2) for (g1,g2) in zip(d_grads_real, d_grads_noise)]

    # G step, run multiple steps (5) on the same minibatch to produce higher loss 

    g_grads = tf.gradients(g_loss + g_decay, g_vars, stop_gradients=g_vars)
    g_train_op = g_optimizer.apply_gradients(zip(g_grads,g_vars))

    with tf.control_dependencies([g_train_op]):
        x_noise2 = generator(x_real)
        x_perturbed2 = x_real + epsilon*x_noise2

        d_out_noise2 = discriminator(x_perturbed2)
        d_loss_noise2 = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                logits=d_out_noise2, labels=tf.one_hot(label,class_num)))
        g_loss2 = -d_loss_noise2
        g_grads2 = tf.gradients(g_loss2 + g_decay, g_vars, stop_gradients=g_vars)
        g_train_op2 = g_optimizer.apply_gradients(zip(g_grads2, g_vars))

        with tf.control_dependencies([g_train_op2]):
            x_noise3 = generator(x_real)
            x_perturbed3 = x_real + epsilon*x_noise3

            d_out_noise3 = discriminator(x_perturbed3)
            d_loss_noise3 = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                    logits=d_out_noise3, labels=tf.one_hot(label,class_num)))
            g_loss3 = -d_loss_noise3
            g_grads3 = tf.gradients(g_loss3 + g_decay, g_vars, stop_gradients=g_vars)
            g_train_op3 = g_optimizer.apply_gradients(zip(g_grads3, g_vars))


            with tf.control_dependencies([g_train_op3]):
                x_noise4 = generator(x_real)
                x_perturbed4 = x_real + epsilon*x_noise4

                d_out_noise4 = discriminator(x_perturbed4)
                d_loss_noise4 = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                        logits=d_out_noise4, labels=tf.one_hot(label,class_num)))
                g_loss4 = -d_loss_noise4
                g_grads4 = tf.gradients(g_loss4 + g_decay, g_vars, stop_gradients=g_vars)
                g_train_op4 = g_optimizer.apply_gradients(zip(g_grads4, g_vars))


                with tf.control_dependencies([g_train_op4]):
                    x_noise5 = generator(x_real)
                    x_perturbed5 = x_real + epsilon*x_noise5

                    d_out_noise5 = discriminator(x_perturbed5)
                    d_loss_noise5 = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                            logits=d_out_noise5, labels=tf.one_hot(label,class_num)))
                    g_loss5 = -d_loss_noise5
                    g_grads5 = tf.gradients(g_loss5 + g_decay, g_vars, stop_gradients=g_vars)
                    g_train_op5 = g_optimizer.apply_gradients(zip(g_grads5, g_vars))


                    with tf.control_dependencies([g_train_op5]):
                        x_noise6 = generator(x_real)
                        x_perturbed6 = x_real + epsilon*x_noise6

                        d_out_noise6 = discriminator(x_perturbed6)
                        d_loss_noise6 = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
                            logits=d_out_noise6, labels=tf.one_hot(label,class_num)))
                        g_loss6 = -d_loss_noise6
                        g_grads6 = tf.gradients(g_loss6 + g_decay, g_vars, stop_gradients=g_vars)

                        d_grads_noise6 = tf.gradients(d_loss_noise6, d_vars)


    # D step: use finite approx for gradient regularization

    # evaluate Hessian vector approx (Eq. 9 in paper)
    # g_virtual_optimizer computes \phi_k + hv
    g_step_size = learning_rate/10
    g_virtual_optimizer = tf.train.GradientDescentOptimizer(learning_rate=g_step_size)

    with tf.control_dependencies([d_grads_noise6[0], d_grads_real[0]]):
        g_virtual_step = g_virtual_optimizer.apply_gradients(zip(g_grads6, g_vars))

    # make sure g is updated before re-computing 
    with tf.control_dependencies([g_virtual_step]):
        x_noiseB = generator(x_real)

    x_perturbedB = tf.stop_gradient(x_real + epsilon*x_noiseB)

    d_out_noiseB = discriminator(x_perturbedB)
    d_loss_noiseB = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(
        logits=d_out_noiseB, labels=tf.one_hot(label,class_num)))

    d_grads_noiseB = tf.gradients(d_loss_noiseB, d_vars)


    d_grads_full = [g1 + g2 + gamma*(g2-g3)/float(g_step_size) 
                    for (g1,g2,g3) in zip(d_grads_real, d_grads_noise6, d_grads_noiseB)]

    d_train_op = d_optimizer.apply_gradients(zip(d_grads_full, d_vars))
    d_train_op2 = d_optimizer2.apply_gradients(zip(d_grads_full, d_vars))

    # restore parameters for G
    minus_g_grads = [-g for g in g_grads6]
    with tf.control_dependencies([d_train_op]):
        d_train_and_restore_op = g_virtual_optimizer.apply_gradients(zip(minus_g_grads,g_vars))

    with tf.control_dependencies([d_train_op2]):
        d_train_and_restore_op2 = g_virtual_optimizer.apply_gradients(zip(minus_g_grads,g_vars))



    # Gradient norm to evaluate convergence
    d_reg = 0.5 * sum(
        tf.reduce_sum(tf.square(g)) for g in d_grads
    )
    g_reg = 0.5 * sum(
        tf.reduce_sum(tf.square(g)) for g in g_grads
    )

    # Build test
    acc, acc_update, acc_init = build_test(discriminator, test_data, config)
    acc_fgs, acc_update_fgs, acc_init_fgs = build_test_fgs(discriminator, test_data, config)
    acc_pgd, acc_update_pgd, acc_init_pgd = build_test_pgd(discriminator, test_data, config)
    acc_g, acc_update_g, acc_init_g = build_test_G(discriminator, generator, test_data, config)


    # Global step
    global_step = tf.Variable(0, trainable=False)
    global_step_op = global_step.assign_add(1)


    saverD = tf.train.Saver(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='discriminator'))
    saverG = tf.train.Saver(tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='generator'))

    # Supervisor
    sv = tf.train.Supervisor(
        logdir=config['log_dir'], global_step=global_step,
        summary_op=None,
    )


    model_filenameD = config['model_fileD']
    model_filenameG = config['model_fileG']


    with sv.managed_session() as sess:

        num_steps_per_epoch = int(train_size/(batch_size))+1
        
        for batch_idx in range(config['nsteps']):
            if sv.should_stop():
               break

            if batch_idx<100000:
                d_loss_out, g_loss_out, g_loss3_out, g_loss5_out, d_reg_out, g_reg_out, _, _ = sess.run(
                    [d_loss, g_loss, g_loss3, g_loss5, d_reg, g_reg, g_train_op5, d_train_and_restore_op])
            else:
                d_loss_out, g_loss_out, g_loss3_out, g_loss5_out, d_reg_out, g_reg_out, _, _ = sess.run(
                    [d_loss, g_loss, g_loss3, g_loss5, d_reg, g_reg, g_train_op5, d_train_and_restore_op2])

            
            if batch_idx % print_iter==0:
                test_acc = run_test(acc, acc_update, acc_init, sess, config)
                test_acc_fgs = run_test(acc_fgs, acc_update_fgs, acc_init_fgs, sess, config)
                test_acc_pgd = run_test(acc_pgd, acc_update_pgd, acc_init_pgd, sess, config)
                test_acc_g = run_test(acc_g, acc_update_g, acc_init_g, sess, config)

                print('i=%d Loss_g: %4.4f, g_loss3: %.4f, g_loss5: %.4f, Loss_d: %4.4f, acc: %.4f acc_fgs: %.4f '
                    'acc_pgd: %.4f acc_g: %.4f d_reg: %.4f g_reg: %.4f' % (batch_idx, g_loss_out, g_loss3_out, 
                    g_loss5_out, d_loss_out, test_acc, test_acc_fgs, test_acc_pgd, test_acc_g, d_reg_out, g_reg_out), 
                    flush=True)


            if batch_idx % save_iter == 0:
                saverD.save(sess, model_filenameD)
                saverG.save(sess, model_filenameG)

            sess.run(global_step_op)

        saverD.save(sess, model_filenameD)
        saverG.save(sess, model_filenameG)



