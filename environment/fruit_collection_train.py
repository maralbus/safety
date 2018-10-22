
"""
Created on October 1, 2018

@author: mae-ma
@attention: fruit game for the safety DRL package using different architectures
@contact: albus.marcel@gmail.com (Marcel Albus)
@version: 2.0.3

#############################################################################################

History:
- v2.0.3: add more terminal flags
- v2.0.2: add bool for rendering
- v2.0.1: implement click functionality
- v2.0.0: update main function for better structure
- v1.1.1: save reward output in textfile
- v1.1.0: dqn input fixed -> working, overblow input image
- v1.0.2: first dqn test
- v1.0.1: extend path to find other packages
- v1.0.0: first init
"""

import os
from copy import deepcopy
import pygame
import numpy as np
import time
import yaml
import click
import tqdm
# or FruitCollectionLarge or FruitCollectionMini
from fruit_collection import FruitCollection, FruitCollectionSmall, FruitCollectionLarge, FruitCollectionMini

###############################
# Necessary to import packages from different folders
###############################
import sys
import os
sys.path.extend([os.path.split(sys.path[0])[0]])
############################
# architectures
############################
from architectures.a3c import AsynchronousAdvantageActorCritic
from architectures.hra import HybridRewardArchitecture
from architectures.dqn import DeepQNetwork
import architectures.misc as misc
############################

############################
# RGB colors
############################
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
BLUE = (0, 100, 255)
WALL = (80, 80, 80)
############################
np.set_printoptions(threshold=np.nan)


class FruitCollectionTrain(FruitCollection):
    def __init__(self, warmstart=False, simple=True, render=False, testing=False):
        print('–'*100)
        print('–'*100)
        print('Warmstart:\t', warmstart)
        print('Simple:\t\t', simple)
        print('–'*100)
        print('–'*100)

        self.env = FruitCollectionMini(rendering=render, lives=1, is_fruit=True, is_ghost=False, image_saving=False)
        self.env.render()

        self.testing = testing
        # input_dim = (14, 21, 1) # (img_height, img_width, n_channels)
        self.overblow_factor = 8
        self.input_dim = (10, 10)  # (img_height, img_width)
        self.mc = misc
        self.dqn = DeepQNetwork(env=self.env, input_dim=self.input_dim, output_dim=self.env.nb_actions,
                                warmstart=warmstart, warmstart_path='/home/mae-ma/git/safety', 
                                simple_dqn=simple, name='DQN')
        self.a3c = AsynchronousAdvantageActorCritic()
        self.hra = HybridRewardArchitecture()

    def init_with_mode(self):
        self.is_ghost = False
        self.is_fruit = True
        self.reward_scheme = {'ghost': -10.0, 'fruit': +1.0, 'step': 0.0, 'wall': 0.0}
        self.nb_fruits = 4
        self.scr_w = 5
        self.scr_h = 5
        self.possible_fruits = [[0, 0], [0, 4], [4, 0], [4, 4]]
        self.rendering_scale = 50
        self.walls = [[1, 0], [2, 0], [4, 1], [0, 2], [2, 2], [3, 3], [1, 4]]
        if self.is_ghost:
            self.ghosts = [{'colour': RED, 'reward': self.reward_scheme['ghost'], 'location': [0, 1],
                            'active': True}]
        else:
            self.ghosts = []

    def _reset_targets(self):
        while True:
            self.player_pos_x, self.player_pos_y = np.random.randint(0, self.scr_w), np.random.randint(0, self.scr_h)
            if [self.player_pos_x, self.player_pos_y] not in self.possible_fruits + self.walls:
                break
        self.fruits = []
        self.active_fruits = []
        if self.is_fruit:
            for x in range(self.scr_w):
                for y in range(self.scr_h):
                    self.fruits.append({'colour': BLUE, 'reward': self.reward_scheme['fruit'],
                                        'location': [x, y], 'active': False})
                    self.active_fruits.append(False)
            fruits_idx = deepcopy(self.possible_fruits)
            np.random.shuffle(fruits_idx)
            fruits_idx = fruits_idx[:self.nb_fruits]
            self.mini_target = [False] * len(self.possible_fruits)
            for f in fruits_idx:
                idx = f[1] * self.scr_w + f[0]
                self.fruits[idx]['active'] = True
                self.active_fruits[idx] = True
                self.mini_target[self.possible_fruits.index(f)] = True


    def main(self, verbose=False):
        reward = []
        step_counter = 0
        for epoch in range(self.dqn.num_epochs):
            for episode in range(self.dqn.num_episodes):
                states = []
                self.env.reset()
                rew = 0
                framerate = 100
                sleep_sec = 1 / framerate
                for t in tqdm.tqdm(range(self.dqn.num_steps), unit='Episodes', ncols=100):
                    # fix framerate
                    time.sleep(sleep_sec)
                    if t == 0:
                        action = np.random.choice(self.env.legal_actions)
                    else:
                        action = self.dqn.act(states[-1])
                        self.dqn.calc_eps_decay(step_counter=step_counter)
                    obs, r, terminated, info = self.env.step(action)
                    state_low = obs[2, ...]
                    # state_high = mc.overblow(input_array=state_low, factor=overblow_factor)
                    # state = state_high.reshape(input_dim)
                    # append grayscale image to state list
                    # states.append(self.mc.make_frame(obs, do_overblow=True, overblow_factor=self.overblow_factor))
                    states.append(self.mc.make_frame(obs, do_overblow=False, 
                                                     overblow_factor=self.overblow_factor)
                                                     .flatten().reshape(-1, 100))
                    # states.append(state)
                    if t >= 1:
                        state_t = states[-2]
                        state_t1 = states[-1]
                        self.dqn.remember(state=state_t, action=action, reward=r, next_state=state_t1, done=terminated)
                        self.dqn.do_training(is_testing=self.testing)

                    # if r != 0:
                    #     self.dqn.remember(state=state_t, action=action, reward=r, next_state=state_t1, done=terminated)

                    self.env.render()
                    # increase step counter
                    step_counter += 1

                    if verbose:
                        print("\033[2J\033[H\033[2J", end="")
                        print()
                        print('pos: ', self.env.player_pos_x, self.env.player_pos_y)
                        print('reward: ', r)
                        print('state:')
                        print(state_low)
                        print('─' * 30)
                        print('─' * 30)

                    # update target model
                    if step_counter % self.dqn.params['target_network_update_frequency'] == 0:
                        self.dqn.update_target_model(soft=False, beta=0.8)
                        if verbose:
                            print('\n' + '–' * 50)
                            print('update after',
                                  self.dqn.params['target_network_update_frequency'], 'steps')
                            print('–' * 50)

                    if t == self.dqn.num_steps - 1:
                        terminated = True
                    if terminated is False:
                        rew += r
                    if terminated is True:
                        rew += r
                        # self.dqn.do_training(is_testing=False)
                        self.dqn.save_buffer(path='replay_buffer.pkl')
                        self.dqn.save_weights(path='weights.h5')
                        print('episode: {}/{} \nepoch: {}/{} \nscore: {} \neps: {:.3f} \nsum of steps: {}'.
                              format(episode, self.dqn.num_episodes, epoch,
                                     self.dqn.num_epochs, rew, self.dqn.epsilon, step_counter))
                        reward.append((rew, step_counter))
                        with open('reward.yml', 'w') as f:
                            yaml.dump(reward, f)
                        break

@click.command()
@click.option('--warmstart/--no-warmstart', '-w/-nw', default=False, help='load the network weights')
@click.option('--simple/--no-simple', '-s/-ns', default=True, help='uses simple DQN network')
@click.option('--render/--no-render', '-r/-nr', default=False, help='render the pygame')
@click.option('--testing/--no-testing', '-t/-nt', default=False, help='test the network')
def run(warmstart, simple, render, testing):
    fct = FruitCollectionTrain(warmstart=warmstart, simple=simple, render=render, testing=testing)
    fct.main(verbose=False)

if __name__ == '__main__':
    run()
