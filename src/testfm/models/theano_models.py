from testfm.models.cutil.interface import IModel

__author__ = 'linas'

'''
Models using Theano. Taken from: http://www.deeplearning.net/
'''

import math, time
import numpy
import theano
from theano import tensor as T
from theano.tensor.shared_randomstreams import RandomStreams
from testfm.models.cutil.interface import IModel


try:
    from functools import lru_cache
except ImportError:
    from backports.functools_lru_cache import lru_cache

class RBM(IModel):
    """
    Restricted Boltzmann Machines (RBM) is used in the CF as one of the most successful model
    for collaborative filtering:

    Ruslan Salakhutdinov, Andriy Mnih, Geoffrey E. Hinton:
        Restricted Boltzmann machines for collaborative filtering. ICML 2007: 791-798

    The idea, is that you represent each user by vector of watched/unwatched movies.
    You train the RBM using such data, i.e., each user is an example for RBM.
    So RBM has n_visible equal to the #movies. The number of hidden units is a hyper-parameter.

    You make predictions for a user by providing a vector of
    his movies and doing gibbs sampling via visible-hidden-visible states.
    The activation levels on visible will be your predictions.

    The implementation is taken from http://www.deeplearning.net/tutorial/rbm.html

    """
    def __init__(self, n_visible=None, n_hidden=100, input=None, learning_rate=0.1, training_epochs=5,\
                 W=None, hbias=None, vbias=None, numpy_rng=None, theano_rng=None):
        """
        RBM constructor. Defines the parameters of the model along with
        basic operations for inferring hidden from visible (and vice-versa),
        as well as for performing CD updates.

        :param input: None for standalone RBMs or symbolic variable if RBM is
        part of a larger graph.

        :param n_visible: number of visible units

        :param n_hidden: number of hidden units

        :param W: None for standalone RBMs or symbolic variable pointing to a
        shared weight matrix in case RBM is part of a DBN network; in a DBN,
        the weights are shared between RBMs and layers of a MLP

        :param hbias: None for standalone RBMs or symbolic variable pointing
        to a shared hidden units bias vector in case RBM is part of a
        different network

        :param vbias: None for standalone RBMs or a symbolic variable
        pointing to a shared visible units bias
        """

        #the problem that we don't need this parameter as it will be set automatically
        if n_visible is None:
            n_visible = 0

        self.n_visible = n_visible
        self.n_hidden = n_hidden
        self.learning_rate = learning_rate
        self.training_epochs = training_epochs

        if numpy_rng is None:
            # create a number generator
            numpy_rng = numpy.random.RandomState(1234)

        if theano_rng is None:
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))

        if W is None:
            # W is initialized with `initial_W` which is uniformely
            # sampled from -4*sqrt(6./(n_visible+n_hidden)) and
            # 4*sqrt(6./(n_hidden+n_visible)) the output of uniform if
            # converted using asarray to dtype theano.config.floatX so
            # that the code is runable on GPU
            initial_W = numpy.asarray(numpy_rng.uniform(
                      low=float(-4.0 * math.sqrt(6.0 / float(n_hidden + n_visible))),
                      high=float(4.0 * math.sqrt(6.0 / float(n_hidden + n_visible))),
                      size=(n_visible, n_hidden)),
                      dtype=theano.config.floatX)
            # theano shared variables for weights and biases
            W = theano.shared(value=initial_W, name='W', borrow=True)

        if hbias is None:
            # create shared variable for hidden units bias
            hbias = theano.shared(value=numpy.zeros(n_hidden,
                                                    dtype=theano.config.floatX),
                                  name='hbias', borrow=True)

        if vbias is None:
            # create shared variable for visible units bias
            vbias = theano.shared(value=numpy.zeros(n_visible,
                                                    dtype=theano.config.floatX),
                                  name='vbias', borrow=True)

        # initialize input layer for standalone RBM or layer0 of DBN
        self.input = input
        if not input:
            self.input = T.matrix('input')

        self.W = W
        self.hbias = hbias
        self.vbias = vbias
        self.theano_rng = theano_rng
        # **** WARNING: It is not a good idea to put things in this list
        # other than shared variables created in this function.
        self.params = [self.W, self.hbias, self.vbias]

    def free_energy(self, v_sample):
        ''' Function to compute the free energy '''
        wx_b = T.dot(v_sample, self.W) + self.hbias
        vbias_term = T.dot(v_sample, self.vbias)
        hidden_term = T.sum(T.log(1 + T.exp(wx_b)), axis=1)
        return -hidden_term - vbias_term

    def propup(self, vis):
        '''This function propagates the visible units activation upwards to
        the hidden units

        Note that we return also the pre-sigmoid activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(vis, self.W) + self.hbias
        return [pre_sigmoid_activation, T.nnet.sigmoid(pre_sigmoid_activation)]

    def sample_h_given_v(self, v0_sample):
        ''' This function infers state of hidden units given visible units '''
        # compute the activation of the hidden units given a sample of
        # the visibles
        pre_sigmoid_h1, h1_mean = self.propup(v0_sample)
        # get a sample of the hiddens given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        h1_sample = self.theano_rng.binomial(size=h1_mean.shape,
                                             n=1, p=h1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_h1, h1_mean, h1_sample]

    def propdown(self, hid):
        '''This function propagates the hidden units activation downwards to
        the visible units

        Note that we return also the pre_sigmoid_activation of the
        layer. As it will turn out later, due to how Theano deals with
        optimizations, this symbolic variable will be needed to write
        down a more stable computational graph (see details in the
        reconstruction cost function)

        '''
        pre_sigmoid_activation = T.dot(hid, self.W.T) + self.vbias
        return [pre_sigmoid_activation, T.nnet.sigmoid(pre_sigmoid_activation)]

    def sample_v_given_h(self, h0_sample):
        ''' This function infers state of visible units given hidden units '''
        # compute the activation of the visible given the hidden sample
        pre_sigmoid_v1, v1_mean = self.propdown(h0_sample)
        # get a sample of the visible given their activation
        # Note that theano_rng.binomial returns a symbolic sample of dtype
        # int64 by default. If we want to keep our computations in floatX
        # for the GPU we need to specify to return the dtype floatX
        v1_sample = self.theano_rng.binomial(size=v1_mean.shape,
                                             n=1, p=v1_mean,
                                             dtype=theano.config.floatX)
        return [pre_sigmoid_v1, v1_mean, v1_sample]

    def gibbs_hvh(self, h0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the hidden state'''
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h0_sample)
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v1_sample)
        return [pre_sigmoid_v1, v1_mean, v1_sample,
                pre_sigmoid_h1, h1_mean, h1_sample]

    def gibbs_vhv(self, v0_sample):
        ''' This function implements one step of Gibbs sampling,
            starting from the visible state'''
        pre_sigmoid_h1, h1_mean, h1_sample = self.sample_h_given_v(v0_sample)
        pre_sigmoid_v1, v1_mean, v1_sample = self.sample_v_given_h(h1_sample)
        return [pre_sigmoid_h1, h1_mean, h1_sample,
                pre_sigmoid_v1, v1_mean, v1_sample]

    def get_cost_updates(self, lr=0.1, persistent=None, k=1):
        """This functions implements one step of CD-k or PCD-k

        :param lr: learning rate used to train the RBM

        :param persistent: None for CD. For PCD, shared variable
            containing old state of Gibbs chain. This must be a shared
            variable of size (batch size, number of hidden units).

        :param k: number of Gibbs steps to do in CD-k/PCD-k

        Returns a proxy for the cost and the updates dictionary. The
        dictionary contains the update rules for weights and biases but
        also an update of the shared variable used to store the persistent
        chain, if one is used.

        """

        # compute positive phase
        pre_sigmoid_ph, ph_mean, ph_sample = self.sample_h_given_v(self.input)

        # decide how to initialize persistent chain:
        # for CD, we use the newly generate hidden sample
        # for PCD, we initialize from the old state of the chain
        if persistent is None:
            chain_start = ph_sample
        else:
            chain_start = persistent

        # perform actual negative phase
        # in order to implement CD-k/PCD-k we need to scan over the
        # function that implements one gibbs step k times.
        # Read Theano tutorial on scan for more information :
        # http://deeplearning.net/software/theano/library/scan.html
        # the scan will return the entire Gibbs chain
        [pre_sigmoid_nvs, nv_means, nv_samples,
         pre_sigmoid_nhs, nh_means, nh_samples], updates = \
            theano.scan(self.gibbs_hvh,
                    # the None are place holders, saying that
                    # chain_start is the initial state corresponding to the
                    # 6th output
                    outputs_info=[None,  None,  None, None, None, chain_start],
                    n_steps=k)

        # determine gradients on RBM parameters
        # not that we only need the sample at the end of the chain
        chain_end = nv_samples[-1]

        cost = T.mean(self.free_energy(self.input)) - T.mean(
            self.free_energy(chain_end))
        # We must not compute the gradient through the gibbs sampling
        gparams = T.grad(cost, self.params, consider_constant=[chain_end])

        # constructs the update dictionary
        for gparam, param in zip(gparams, self.params):
            # make sure that the learning rate is of the right dtype
            updates[param] = param - gparam * T.cast(lr,
                                                    dtype=theano.config.floatX)
        if persistent:
            # Note that this works only if persistent is a shared variable
            updates[persistent] = nh_samples[-1]
            # pseudo-likelihood is a better proxy for PCD
            monitoring_cost = self.get_pseudo_likelihood_cost(updates)
        else:
            # reconstruction cross-entropy is a better proxy for CD
            monitoring_cost = self.get_reconstruction_cost(updates,
                                                           pre_sigmoid_nvs[-1])

        return monitoring_cost, updates

    def get_pseudo_likelihood_cost(self, updates):
        """Stochastic approximation to the pseudo-likelihood"""

        # index of bit i in expression p(x_i | x_{\i})
        bit_i_idx = theano.shared(value=0, name='bit_i_idx')

        # binarize the input image by rounding to nearest integer
        xi = T.round(self.input)

        # calculate free energy for the given bit configuration
        fe_xi = self.free_energy(xi)

        # flip bit x_i of matrix xi and preserve all other bits x_{\i}
        # Equivalent to xi[:,bit_i_idx] = 1-xi[:, bit_i_idx], but assigns
        # the result to xi_flip, instead of working in place on xi.
        xi_flip = T.set_subtensor(xi[:, bit_i_idx], 1 - xi[:, bit_i_idx])

        # calculate free energy with bit flipped
        fe_xi_flip = self.free_energy(xi_flip)

        # equivalent to e^(-FE(x_i)) / (e^(-FE(x_i)) + e^(-FE(x_{\i})))
        cost = T.mean(self.n_visible * T.log(T.nnet.sigmoid(fe_xi_flip -
                                                            fe_xi)))

        # increment bit_i_idx % number as part of updates
        updates[bit_i_idx] = (bit_i_idx + 1) % self.n_visible

        return cost

    def get_reconstruction_cost(self, updates, pre_sigmoid_nv):
        """Approximation to the reconstruction error

        Note that this function requires the pre-sigmoid activation as
        input.  To understand why this is so you need to understand a
        bit about how Theano works. Whenever you compile a Theano
        function, the computational graph that you pass as input gets
        optimized for speed and stability.  This is done by changing
        several parts of the subgraphs with others.  One such
        optimization expresses terms of the form log(sigmoid(x)) in
        terms of softplus.  We need this optimization for the
        cross-entropy since sigmoid of numbers larger than 30. (or
        even less then that) turn to 1. and numbers smaller than
        -30. turn to 0 which in terms will force theano to compute
        log(0) and therefore we will get either -inf or NaN as
        cost. If the value is expressed in terms of softplus we do not
        get this undesirable behaviour. This optimization usually
        works fine, but here we have a special case. The sigmoid is
        applied inside the scan op, while the log is
        outside. Therefore Theano will only see log(scan(..)) instead
        of log(sigmoid(..)) and will not apply the wanted
        optimization. We can not go and replace the sigmoid in scan
        with something else also, because this only needs to be done
        on the last step. Therefore the easiest and more efficient way
        is to get also the pre-sigmoid activation as an output of
        scan, and apply both the log and sigmoid outside scan such
        that Theano can catch and optimize the expression.

        """

        cross_entropy = T.mean(
                T.sum(self.input * T.log(T.nnet.sigmoid(pre_sigmoid_nv)) +
                (1 - self.input) * T.log(1 - T.nnet.sigmoid(pre_sigmoid_nv)),
                      axis=1))

        return cross_entropy

    @classmethod
    def param_details(cls):
        return {
            "learning_rate": (0.01, 0.05, 0.5, 0.1),
            "training_epochs": (1, 50, 5, 15),
            "n_hidden": (10, 1000, 10, 100),
        }

    def get_name(self):
        return "RBM (n_hidden={0})".format(self.n_hidden)

    def _convert(self, training_data):
        iid_map = {item: id for id, item in enumerate(training_data.item.unique())}
        uid_map = {user: id for id, user in enumerate(training_data.user.unique())}
        users = {user: set(entries) for user, entries in training_data.groupby('user')['item']}

        train_set_x = numpy.zeros((len(uid_map), len(iid_map)))
        for user, items in users.items():
            for i in items:
                train_set_x[uid_map[user], iid_map[i]] = 1

        return train_set_x, uid_map, iid_map, users

    def fit(self, training_data):
        '''
        Converts training_data pandas data frame into the RBM representation.
        The representation contains vector of movies for each user.
        '''

        matrix, uid_map, iid_map, user_data = self._convert(training_data)
        self.user_data = user_data
        self.uid_map = uid_map
        self.iid_map = iid_map

        training_set_x = theano.shared(matrix, name='training_data')
        self.input = training_set_x
        self.train_rbm(training_set_x)

    def train_rbm(self, train_set_x, batch_size=20):
        """
        Trains the RBM, given the training data set.

        :param train_set_x: a matrix of user vectors for trainign RBM
        :param learning_rate: learning rate used for training the RBM
        :param training_epochs: number of epochs used for training
        :param batch_size: size of a batch used to train the RBM
        :param n_chains: number of parallel Gibbs chains to be used for sampling
        :param n_samples: number of samples to plot for each chain

        """
        # compute number of minibatches for training, validation and testing
        n_train_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size

        # allocate symbolic variables for the data
        index = T.lscalar()    # index to a [mini]batch
        x = T.matrix('x')  # the data is presented as user vector per each row

        rng = numpy.random.RandomState(123)
        theano_rng = RandomStreams(rng.randint(2 ** 30))

        # initialize storage for the persistent chain (state = hidden layer of chain)
        persistent_chain = theano.shared(numpy.zeros((batch_size, self.n_hidden), dtype=theano.config.floatX), borrow=True)

        # construct the RBM class
        self.rbm = RBM(n_visible=train_set_x.shape[1].eval(), n_hidden=self.n_hidden, input=x, numpy_rng=rng, theano_rng=theano_rng)

        # get the cost and the gradient corresponding to one step of CD-15
        cost, updates = self.rbm.get_cost_updates(lr=self.learning_rate, persistent=persistent_chain, k=self.training_epochs)

        #################################
        #     Training the RBM          #
        #################################
        # it is ok for a theano function to have no output
        # the purpose of train_rbm is solely to update the RBM parameters
        train_rbm = theano.function([index], cost, updates=updates, givens={x: train_set_x[index * batch_size: (index + 1) * batch_size]}, name='train_rbm')

        start_time = time.clock()
        # go through training epochs
        for epoch in xrange(self.training_epochs):
            # go through the training set
            mean_cost = []
            for batch_index in xrange(n_train_batches):
                mean_cost += [train_rbm(batch_index)]
            print 'Training epoch %d, cost is ' % epoch, numpy.mean(mean_cost)

        end_time = time.clock()
        pretraining_time = (end_time - start_time)
        print ('Training took %f minutes' % (pretraining_time / 60.))

    @lru_cache(maxsize=1000)
    def _get_user_predictions(self, user):
        '''
        Compute the prediction for the user. It is cashed as we need to do it many times for evaluation.
        '''

        #print "computing prediction vector for user ",user

        user_items = self.user_data[user]

        matrix = numpy.zeros((1, len(self.iid_map))) #just one user for whoem we are making prediction

        #initialize the vector with items that user has experienced
        for i in user_items:
            matrix[0, self.iid_map[i]] = 1
        test_x = theano.shared(matrix, name='test_user')

        #number_of_times_up_down = 100
        # define one step of Gibbs sampling (mf = mean-field) define a
        presig_hids, hid_mfs, hid_samples, presig_vis, vis_mfs, vis_samples = self.rbm.gibbs_vhv(test_x)

        # [presig_hids, hid_mfs, hid_samples, presig_vis, vis_mfs, vis_samples], updates =  \
        #                     theano.scan(
        #                         self.gibbs_vhv,
        #                         outputs_info=[None, None, None, None, None, test_x],
        #                         n_steps=number_of_times_up_down)

        # add to updates the shared variable that takes care of our persistent
        # chain :.
        # get the cost and the gradient corresponding to one step of CD-15
        # updates.update({test_x: vis_samples[-1]})
        # construct the function that implements our persistent chain.
        # we generate the "mean field" activations for plotting and the actual
        # samples for reinitializing the state of our persistent chain
        # sample_fn = theano.function([], [vis_mfs[-1], vis_samples[-1]],
        #                             updates=updates,
        #                             name='sample_fn')

        #predictions, vis_sample = sample_fn()
        #we call eval, in order to save ndarray and not the pointer to some theano internal representation
        return vis_mfs.eval()

    def get_score(self, user, item):

        #lets initialize visible layer to a user
        iid = self.iid_map[item]

        user_pred = self._get_user_predictions(user)
        return user_pred[0, iid]
