
"""
Created on October 1, 2018

@author: mae-ma
@attention: architectures for the safety DRL package
@contact: albus.marcel@gmail.com (Marcel Albus)
@version: 1.0.0

#############################################################################################

History:
- v1.0.0: first init
"""

import numpy as np
import os
import yaml
import tensorflow as tf
from tensorflow import keras
import threading
import time
from matplotlib import pyplot as plt

from architectures.replay_buffer import ReplayBuffer
import architectures.misc as misc
from architectures.misc import Font
from architectures.agent import Agent


ACTOR = 0
CRITIC = 1
scores = []

class AsynchronousAdvantageActorCriticGlobal(Agent):
    def __init__(self,
                 input_shape: tuple = (10, 10),
                 output_dim: int = 4,
                 warmstart: bool = False,
                 warmstart_path: str = None,
                 simple_a3c: bool = True,
                 params: dict = None,
                 env=None) -> None:
        """
        Input:
            input_shape (int): Input shape of format type (n_img, img_height, img_width, n_channels)
            output_dim (int): Output dimension
            warmstart (bool): load network weights from disk
            warmstart_path (str): path where the weights are stored
            simple_a3c (bool): use simplified network
            params (dict): parameter dictionary with all config values
            env: fruit game environment
        """
        super(AsynchronousAdvantageActorCriticGlobal,
              self).__init__(parameters=params)
        self.session = tf.InteractiveSession()
        keras.backend.set_session(self.session)
        self.session.run(tf.global_variables_initializer())
        
        self.params = params
        self.print_params(architecture_name='A3C')

        self.rng = np.random.RandomState(self.params['random_seed'])
        self.simple_a3c = simple_a3c

        if self.simple_a3c:
            # input shape = (img_height * img_width, )
            self.input_shape = input_shape
        else:
            # input shape = (img_height, img_width)
            self.input_shape = input_shape + (1,)  # (height, width, channels=1)
        
        if env is None:
            raise ValueError('Please provide an environment')
        else:
            self.env = env
        self.output_dim = output_dim  # number of actions
        self.l_rate = self.params['learning_rate']
        self.minibatch_size = self.params['minibatch_size']
        self.gamma = self.params['gamma']
        self.n_step_return = self.params['n_step_return']
        self.epsilon = self.params['epsilon']
        self.epsilon_min = self.params['epsilon_min']
        self.threads = self.params['threads']
        # debug flag
        self.debug = False
        self.delete_training_log(architecture='A3C')

        self.csv_logger = keras.callbacks.CSVLogger(
            'training_log_A3C.csv', append=True)

        # build neural nets
        self.actor, self.critic = self._build_network()

        self.optimizer = [self._actor_optimizer(), self._critic_optimizer()]

        self.save_model_yaml(architecture='A3C')

        # do warmstart
        self.warmstart_flag = warmstart
        if self.warmstart_flag:
            self.warmstart(warmstart_path)
        

    def _build_network(self) -> keras.models.Sequential:
        """
        build network with A3C parameters
        Output:
            network (keras.model): neural net for architecture
        """
        model = keras.Sequential()

        if self.simple_a3c:
            input_shape = (None, ) + self.input_shape
            # layer_input = keras.Input(batch_shape=(None, self.input_shape))
            layer_input = keras.Input(batch_shape=(None, 100), name='input')
            l_dense = keras.layers.Dense(
                250, activation='relu', kernel_initializer='he_uniform', name='dense')(layer_input)

            out_actions = keras.layers.Dense(self.output_dim, activation='softmax', name='out_a')(l_dense)
            out_value = keras.layers.Dense(
                1, activation='linear', kernel_initializer='he_uniform', name='out_v')(l_dense)

            model = keras.Model(inputs=[layer_input], outputs=[out_actions, out_value])
            actor = keras.Model(inputs=layer_input, outputs=out_actions)
            critic = keras.Model(inputs=layer_input, outputs=out_value)
            actor._make_predict_function()
            critic._make_predict_function()
            model.summary()
            self.model_yaml = model.to_yaml()
            
            if self.debug:
                print(Font.yellow + '–' * 100 + Font.end)
                print(Font.yellow + 'Model: ' + Font.end)
                print(model.to_yaml())
                print(Font.yellow + '–' * 100 + Font.end)

        else:
            # first hidden layer
            # input shape = (img_height, img_width, n_channels)
            layer_input = keras.Input(shape=self.input_shape)
            l_hidden1 = keras.layers.Conv2D(filters=16, kernel_size=(8, 8),
                                            strides=4, activation='relu',
                                            kernel_initializer='he_uniform', data_format='channels_last')(layer_input)
            # second hidden layer
            l_hidden2 = keras.layers.Conv2D(filters=32, kernel_size=(4, 4),
                                            strides=2, activation='relu', kernel_initializer='he_uniform')(l_hidden1)
            # third hidden layer
            l_flatten = keras.layers.Flatten()(l_hidden2)
            l_full1 = keras.layers.Dense(
                256, activation='relu', kernel_initializer='he_uniform')(l_flatten)
            out_actions = keras.layers.Dense(
                self.output_dim, activation='softmax', kernel_initializer='he_uniform')(l_full1)
            out_value = keras.layers.Dense(
                1, activation='linear', kernel_initializer='he_uniform')(l_full1)

            model = keras.Model(inputs=[layer_input], outputs=[out_actions, out_value])
            actor = keras.Model(inputs=layer_input, outputs=out_actions)
            critic = keras.Model(inputs=layer_input, outputs=out_value)
            actor._make_predict_function()
            critic._make_predict_function()
            model.summary()
            self.model_yaml = model.to_yaml()
            
            if self.debug:
                print(Font.yellow + '–' * 100 + Font.end)
                print(Font.yellow + 'Model: ' + Font.end)
                print(model.to_yaml())
                print(Font.yellow + '–' * 100 + Font.end)

        return actor, critic


    def _actor_optimizer(self):
        """
        make loss function for policy gradient
        backpropagation input: 
            [ log(action_probability) * advantages]
        with:
            advantages = discounted_reward - values
        """
        action = keras.backend.placeholder(shape=(self.output_dim,))
        advantages = keras.backend.placeholder(shape=(None, ))

        policy = self.actor.output

        log_prob = tf.log( tf.reduce_sum(policy * action, axis=1, keep_dims=True) + 1e-10)
        loss_policy = - log_prob * tf.stop_gradient(advantages)
        loss = - tf.reduce_sum(loss_policy)
        
        entropy = self.params['loss_entropy_coefficient'] * tf.reduce_sum(policy * tf.log(policy + 1e-10), axis=1, keep_dims=True)
        
        loss_actor = tf.reduce_mean(loss + entropy)

        optimizer = keras.optimizers.RMSprop(lr=self.l_rate,
                                            rho=0.9)
        # optimizer = tf.train.RMSPropOptimizer(learning_rate=self.l_rate, decay=0.9)
        updates = optimizer.get_updates(loss_actor, self.actor.trainable_weights)
        train = keras.backend.function([self.actor.input, action, advantages], [], updates=updates)
        # minimize = optimizer.minimize(loss_actor)
        return train
        # return action, advantages, minimize
    
    def _critic_optimizer(self):
        """
        make loss function for value approximation
        """
        discounted_reward = keras.backend.placeholder(shape=(None, ))

        value = self.critic.output
        loss_value = tf.reduce_mean( tf.square( discounted_reward - value))

        optimizer = keras.optimizers.RMSprop(lr=self.l_rate,
                                             rho=0.9)
        updates = optimizer.get_updates(loss_value, self.critic.trainable_weights)
        train = keras.backend.function([self.critic.input, discounted_reward], [], updates=updates)

        # optimizer = tf.train.RMSPropOptimizer(learning_rate=self.l_rate, decay=0.9)
        # minimize = optimizer.minimize(loss_value)
        return train
        # return discounted_reward, minimize


    # def optimize(self):
    #     if len(self.train_queue[0]) < self.minibatch_size:
    #         time.sleep(0)
    #         return
# 
    #     with self.lock_queue:
    #         if len(self.train_queue[0]) < self.minibatch_size:
    #             s, a, r, s_, s_mask = self.train_queue
    #             # s, a, r, s', s' terminal mask
    #             self.train_queue = [[], [], [], [], []]
    #     
    #     s = np.vstack(s)
    #     a = np.vstack(a)
    #     r = np.vstack(r)
    #     s_ = np.vstack(s_)
    #     s_mask = np.vstack(s_mask)
# 
    #     value = self.critic.predict(s_)
    #     r = r + self.gamma ** self.n_step_return * value * s_mask


    def predict(self, state) -> (np.array, np.array):
        p = self.actor.predict(state)
        v = self.critic.predict(state)
        return p, v
    
    def predict_p(self, state) -> np.array:
        p = self.actor.predict(state)
        return p

    def predict_v(self, state) -> np.array:
        v = self.critic.predict(state)
        return v


    def warmstart(self, path: str) -> None:
        """
        reading weights from disk
        Input:
            path (str): path from where to read the weights
        """
        print(Font.yellow + '–' * 100 + Font.end)
        print(Font.yellow + 'Warmstart, load weights from: ' +
              os.path.join(path, 'actor_weights.h5') + Font.end)
        print(Font.yellow + 'Setting epsilon to eps_min: ' +
              str(self.epsilon_min) + Font.end)
        print(Font.yellow + '–' * 100 + Font.end)
        self.epsilon = self.epsilon_min
        self.actor.load_weights(os.path.join(path, 'actor_weights.h5'))
        self.critic.load_weights(os.path.join(path, 'critic_weights.h5'))


    def do_training(self):
        agents = [AsynchronousAdvantageActorCriticAgent(index=i, 
                                                        actor=self.actor, 
                                                        critic=self.critic, 
                                                        optimizer=self.optimizer, 
                                                        env=self.env, 
                                                        action_dim=self.output_dim, 
                                                        state_shape=self.input_shape, 
                                                        params=self.params) for i in range(self.threads)]
        
        for agent in agents:
            agent.start()

        while True:
            time.sleep(10)
            plot = [np.mean(scores[n:n+500]) for n in range(0, len(scores)-500)]
            plt.figure(figsize=(16,12))
            plt.plot(range(len(plot)), plot, 'b')
            plt.xlabel('Episodes/500')
            plt.ylabel('Mean scores over last 500 episodes')
            plt.grid()
            plt.savefig('./a3c.pdf')

        time.sleep(31)
        for agent in agents:
            agent.stop()
        for agent in agents:
            agent.join()


    def reset_gradients(self):
        r"""
        set gradients $d\theta <- 0$ and $d\theta_v <- 0$
        """
        pass

    def synchronize_from_parameter_server(self):
        r"""
        synchronize thread-specific parameters $\theta' = \theta$ and $\theta_v ' = \theta_v $
        """
        pass

    def accumulate_gradients(self):
        pass


    def save_weights(self, path: str) -> None:
        """
        save model weights
        Input:
            path (str): filepath
        """
        self.actor.save_weights(filepath='actor_'+ path)
        self.critic.save_weights(filepath='critic_' + path)

    def main(self):
        print('A3C here')
        print('–' * 30)


class AsynchronousAdvantageActorCriticAgent(threading.Thread):
    def __init__(self,
                 index: int,
                 actor,
                 critic,
                 optimizer,
                 env,
                 action_dim: int,
                 state_shape: tuple,
                 params: dict = None) -> None:
        
        super(AsynchronousAdvantageActorCriticAgent, self).__init__()
        
        self.index = index
        self.actor = actor
        self.critic = critic
        self.env = env
        self.action_dim = action_dim
        self.state_shape = state_shape
        self.optimizer = optimizer
        self.mc = misc
        self.overblow_factor = 8

        # s, a, r, s_, terminal mask
        self.train_queue = [[], [], [], [], []]

        self.params = params
        self.gamma = self.params['gamma']
        self.num_epochs = self.params['num_epochs']
        self.num_episodes = self.params['num_episodes']
        self.num_steps = self.params['num_steps']
        self.epsilon = self.params['epsilon']
        self.epsilon_min = self.params['epsilon_min']
        self.n_step_return = self.params['n_step_return']
        self.rng = np.random.RandomState(self.params['random_seed'])
        self.debug = False

    def run(self):
        episode = 0
        step_counter = 0
        q_vals = 0
        for epoch in range(self.num_epochs):
            for episode in range(self.num_episodes):
                obs, _, _, _ = self.env.reset()
                state = self.mc.make_frame(obs, do_overblow=False,
                                                overblow_factor=self.overblow_factor,
                                                normalization=False).reshape(self.state_shape)
                rew = 0
                with open('scores.yml', 'w') as f:
                        yaml.dump(scores, f)
                for step in range(self.num_steps):
                    time.sleep(0.001)
                    action, q_vals = self.act(state)
                    obs, r, terminal, info = self.env.step(action)
                    self.calc_eps_decay(step_counter=step_counter)
                    rew += r
                    self.env.render()
                    next_state = self.mc.make_frame(obs, do_overblow=False,
                                                     overblow_factor=self.overblow_factor,
                                                     normalization=False).reshape(self.state_shape)
                    self.memory(state, action, r, next_state, terminal)
                    if step == self.num_steps - 1:
                        terminal = True
                    state = next_state
                    step_counter += 1
                    if terminal:
                        episode += 1
                        self.train_episode()
                        print('\nepisode: {}/{} \nepoch: {}/{} \nscore: {} \neps: {:.3f} \nsum of steps: {}'.
                              format(episode, self.num_episodes, epoch,
                                     self.num_epochs, rew, self.epsilon, step_counter))
                        scores.append(rew)
                        break


    def calc_eps_decay(self, step_counter: int) -> None:
        """
        calculates new epsilon for the given step counter according to the annealing formula
        y = -mx + c
        eps = -(eps - eps_min) * counter + eps
        Input:
            counter (int): step counter
        """
        # if self.warmstart_flag:
        #     self.epsilon = self.epsilon_min
        if self.epsilon > self.epsilon_min:
            self.epsilon = -((self.params['epsilon'] - self.epsilon_min) /
                             self.params['eps_max_frame']) * step_counter + self.params['epsilon']

    def memory(self, state: np.array, action: int, reward: float, next_state: np.array, terminal: float) -> None:
        """
        save <s, a, r, s', terminal> every step
        Input:
            state (np.array): s
            action (int): a
            reward (float): r
            next_state (np.array): s'
            terminal (float): 1.0 if terminal else 0.0
        """
        self.train_queue[0].append(state)
        act = np.zeros(self.action_dim)
        act[action] = 1
        self.train_queue[1].append(act)
        self.train_queue[2].append(reward)
        self.train_queue[3].append(next_state)
        self.train_queue[4].append(1 - terminal)

    def discount_rewards(self, rewards):
        """
        calculate discounted rewards
        Input:
            rewards (list): list of rewards
            done (bool): terminal flag
        """
        discounted_rewards = np.zeros_like(rewards)
        running_rew = 0
        for i in reversed(range(0, len(rewards))):
            running_rew = running_rew * self.gamma + rewards[i]
            discounted_rewards[i] = running_rew
        return discounted_rewards


    def train_episode(self):
        """
        update the policy and target network every episode
        """
        def get_sample(memory, n):
            r = 0.
            for i in range(n):
                r += memory[2][i] * (self.gamma ** i)
            s, a, _, _, t = memory[0][0], memory[1][0], None, None, memory[4][0]
            _, _, _, s_, _ = None, None, None, memory[3][n-1], None
            return s, a, r, s_

        state, action, reward, next_state = [], [], [], []
        i = len(self.train_queue[0])
        while i > 0:
            n = i
            s_q, a_q, r_q, ns_q = get_sample(self.train_queue, n)
            state.append(s_q)
            action.append(a_q)
            reward.append(r_q)
            next_state.append(ns_q)
            i -= 1
        # s, a, r, s_, s_mask = self.train_queue
        s, a, r, s_, s_mask = state, action, reward, next_state, self.train_queue[4]
        s = np.vstack(s)
        a = np.vstack(a)
        r = np.vstack(r)
        s_ = np.vstack(s_)
        s_mask = np.vstack(s_mask)
        v = self.critic.predict(s_)
        r = r + self.gamma ** self.n_step_return * v * s_mask
        
        # shape (len(values), )
        v = np.reshape(v, len(v))
        # advantages = r - v


        discounted_rewards = r
        # discounted_rewards = self.discount_rewards(r)
        # discounted_rewards = discounted_rewards + self.gamma ** self.n_step_return * v * s_mask
        advantages = discounted_rewards - v
        # update policy and value network every episode
        self.optimizer[ACTOR]([s, a, advantages])
        self.optimizer[CRITIC]([s, discounted_rewards])
        # action, advantages, minimize = self.optimizer[ACTOR]
        # discounted_reward, minimize = self.optimizer[CRITIC]
        self.train_queue = [[], [], [], [], []]

    def act(self, state) -> (int, float):
        """
        return action from neural net
        Input:
            state (np.array): the current state as shape (img_height, img_width, 1)
        Output:
            action (int): action number
            policy (float): expected q_value
        """
        s = state.reshape((1, self.state_shape[0]))
        policy = self.actor.predict(s)
        action = misc.eps_greedy(policy[0], self.epsilon, rng=self.rng)
        # return np.random.choice(self.action_dim, size=1, p=policy)
        return action, np.amax(policy[0])


if __name__ == '__main__':
    print('A3C __main__')
