import os
import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from collections import deque
import psycopg2

# -------------------------------
#  Neural Network
# -------------------------------
class DQN(nn.Module):
    def __init__(self, input_dim, output_dim, hidden=512):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, output_dim)
        )

    def forward(self, x):
        return self.net(x)

# -------------------------------
#  Replay Buffer
# -------------------------------
class ReplayBuffer:
    def __init__(self, capacity=500000):
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return (np.array(states), np.array(actions), np.array(rewards),
                np.array(next_states), np.array(dones))

    def __len__(self):
        return len(self.buffer)

# -------------------------------
#  Nemesis using DQN (Improved)
# -------------------------------
class Nemesis:
    TEAM_SIZE = 6
    TOP_K = 200  # action space

    def __init__(self, db_config, device="cpu", model_path="nemesis_dqn.pt"):
        self.db_config = db_config
        self.device = torch.device(device)
        self.model_path = model_path

        # Load data
        self._load_static_data()
        self._build_matchup_matrix()
        self._compute_bst()
        self.valid_action_ids = self._get_top_pokemon(self.TOP_K)
        self.action_to_id = {i: pid for i, pid in enumerate(self.valid_action_ids)}
        self.id_to_action = {pid: i for i, pid in enumerate(self.valid_action_ids)}
        self.action_dim = len(self.valid_action_ids)
        print(f"Action space: {self.action_dim} Pokémon (top {self.TOP_K} by BST).")

        # State: opponent matchup scores (action_dim) + selected mask (action_dim) + team size one-hot (6)
        self.state_dim = 2 * self.action_dim + 6

        self.dqn = DQN(self.state_dim, self.action_dim).to(self.device)
        self.target_dqn = DQN(self.state_dim, self.action_dim).to(self.device)
        self.target_dqn.load_state_dict(self.dqn.state_dict())
        self.optimizer = optim.Adam(self.dqn.parameters(), lr=1e-4)
        self.buffer = ReplayBuffer()

        self.epsilon = 1.0
        self.epsilon_min = 0.1
        self.epsilon_decay = 0.99995   # per step

        if os.path.exists(self.model_path):
            self.load(self.model_path)
            print(f"Loaded pre-trained model from {self.model_path}")
        else:
            print("No saved model found. Train with .train() before using.")
            self.train(episodes=30000)

    # ------------------------------------------------------------------
    #  Database loading (same as before)
    # ------------------------------------------------------------------
    def _get_db_connection(self):
        return psycopg2.connect(**self.db_config)

    def _load_static_data(self):
        conn = self._get_db_connection()
        cur = conn.cursor()
        self.pokemon_types = {}
        cur.execute("SELECT pokemon_id, type_id FROM pokemon_types")
        for pid, tid in cur.fetchall():
            self.pokemon_types.setdefault(pid, []).append(tid)

        self.effectiveness = {}
        cur.execute("SELECT atk_id, def_id, multiplier FROM type_effectiveness")
        for atk, dfn, mul in cur.fetchall():
            self.effectiveness[(atk, dfn)] = float(mul)

        self.pokemon_stats = {}
        cur.execute("SELECT id, hp, atk, def, sp_atk, sp_def, speed FROM pokemon ORDER BY id")
        for row in cur.fetchall():
            self.pokemon_stats[row[0]] = np.array(row[1:], dtype=np.float32)

        self.all_pokemon_ids = list(self.pokemon_stats.keys())
        self.N = len(self.all_pokemon_ids)
        cur.close()
        conn.close()
        print(f"Loaded {self.N} Pokémon from DB.")

    def _compute_bst(self):
        self.bst = {pid: np.sum(stats) for pid, stats in self.pokemon_stats.items()}

    def _get_top_pokemon(self, k):
        sorted_ids = sorted(self.all_pokemon_ids, key=lambda x: self.bst[x], reverse=True)
        return sorted_ids[:k]

    def _build_matchup_matrix(self):
        N = len(self.all_pokemon_ids)
        stats = np.array([self.pokemon_stats[pid] for pid in self.all_pokemon_ids])
        hp, atk, defe, spatk, spdef, spd = stats[:, 0], stats[:, 1], stats[:, 2], stats[:, 3], stats[:, 4], stats[:, 5]
        atk_pow = np.maximum(atk, spatk)
        def_pow = np.maximum(defe, spdef)
        def_pow = np.where(def_pow == 0, 1.0, def_pow)
        hp = np.where(hp == 0, 1.0, hp)

        self.matchup = np.ones((N, N), dtype=np.float32)
        for i, atk_id in enumerate(self.all_pokemon_ids):
            atk_types = self.pokemon_types.get(atk_id, [])
            for j, def_id in enumerate(self.all_pokemon_ids):
                def_types = self.pokemon_types.get(def_id, [])
                if atk_types and def_types:
                    total = sum(self.effectiveness.get((at, dt), 1.0) for at in atk_types for dt in def_types)
                    type_mult = total / (len(atk_types) * len(def_types))
                else:
                    type_mult = 1.0
                damage = (type_mult * atk_pow[i]) / (def_pow[j] * hp[j])
                speed_bonus = 1.1 if spd[i] > spd[j] else 0.9
                self.matchup[i][j] = damage * speed_bonus
        self.matchup = (self.matchup - self.matchup.min()) / (self.matchup.max() - self.matchup.min() + 1e-8)
        print(f"Matchup matrix built: {self.matchup.shape}")

    # ------------------------------------------------------------------
    #  State construction
    # ------------------------------------------------------------------
    def _get_state(self, opponent_ids, current_team_ids):
        opp_indices = [self.all_pokemon_ids.index(pid) for pid in opponent_ids]
        # Part 1: average matchup of each action against opponent
        opp_score = np.zeros(self.action_dim)
        for i, pid in enumerate(self.valid_action_ids):
            idx = self.all_pokemon_ids.index(pid)
            opp_score[i] = self.matchup[idx][opp_indices].mean()

        # Part 2: selected mask
        selected_mask = np.zeros(self.action_dim)
        for pid in current_team_ids:
            if pid in self.id_to_action:
                selected_mask[self.id_to_action[pid]] = 1.0

        # Part 3: team size one-hot
        team_size_vec = np.zeros(6)
        team_size_vec[len(current_team_ids)] = 1.0

        return np.concatenate([opp_score, selected_mask, team_size_vec])

    # ------------------------------------------------------------------
    #  Team score
    # ------------------------------------------------------------------
    def _team_score(self, team_ids, opponent_ids):
        if not team_ids:
            return 0.0
        opp_indices = [self.all_pokemon_ids.index(pid) for pid in opponent_ids]
        team_indices = [self.all_pokemon_ids.index(pid) for pid in team_ids]
        best = self.matchup[team_indices][:, opp_indices].max(axis=0)
        return float(best.mean())

    # ------------------------------------------------------------------
    #  Training (improved)
    # ------------------------------------------------------------------
    def train(self, episodes=20000, batch_size=64, gamma=0.99, target_update=1000):
        print(f"Starting DQN training for {episodes} episodes...")
        steps = 0
        for ep in range(episodes):
            opponent_ids = random.sample(self.all_pokemon_ids, self.TEAM_SIZE)
            current_team = []
            done = False
            episode_reward = 0
            prev_score = 0.0

            while not done:
                state = self._get_state(opponent_ids, current_team)
                # Epsilon-greedy
                if random.random() < self.epsilon:
                    valid_actions = [a for a in range(self.action_dim)
                                     if self.action_to_id[a] not in opponent_ids
                                     and self.action_to_id[a] not in current_team]
                    if not valid_actions:
                        break
                    action = random.choice(valid_actions)
                else:
                    state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                    with torch.no_grad():
                        q_values = self.dqn(state_t).cpu().numpy()[0]
                    for a in range(self.action_dim):
                        pid = self.action_to_id[a]
                        if pid in opponent_ids or pid in current_team:
                            q_values[a] = -np.inf
                    action = np.argmax(q_values)

                chosen_pid = self.action_to_id[action]
                current_team.append(chosen_pid)
                new_score = self._team_score(current_team, opponent_ids)
                reward = new_score - prev_score   # improvement
                if len(current_team) == self.TEAM_SIZE:
                    reward += new_score   # bonus for final team
                    done = True
                else:
                    reward = max(reward, 0.0)  # only positive improvements
                prev_score = new_score

                next_state = self._get_state(opponent_ids, current_team) if not done else np.zeros(self.state_dim)
                self.buffer.push(state, action, reward, next_state, done)
                episode_reward += reward
                steps += 1

                # Decay epsilon per step
                if self.epsilon > self.epsilon_min:
                    self.epsilon *= self.epsilon_decay

                # Update network
                if len(self.buffer) > batch_size:
                    states, actions, rewards, next_states, dones = self.buffer.sample(batch_size)
                    states = torch.FloatTensor(states).to(self.device)
                    actions = torch.LongTensor(actions).to(self.device)
                    rewards = torch.FloatTensor(rewards).to(self.device)
                    next_states = torch.FloatTensor(next_states).to(self.device)
                    dones = torch.FloatTensor(dones).to(self.device)

                    q_values = self.dqn(states).gather(1, actions.unsqueeze(1)).squeeze(1)
                    with torch.no_grad():
                        next_q = self.target_dqn(next_states).max(1)[0]
                        target = rewards + gamma * next_q * (1 - dones)
                    loss = nn.MSELoss()(q_values, target)
                    self.optimizer.zero_grad()
                    loss.backward()
                    self.optimizer.step()

                if steps % target_update == 0:
                    self.target_dqn.load_state_dict(self.dqn.state_dict())

            if ep % 500 == 0:
                print(f"Episode {ep}, epsilon={self.epsilon:.3f}, reward={episode_reward:.3f}, team={current_team[:3]}...")

        self.save(self.model_path)
        print("Training complete. Model saved.")

    # ------------------------------------------------------------------
    #  Inference
    # ------------------------------------------------------------------
    def get_team(self, opponent_ids):
        self.dqn.eval()
        current_team = []
        while len(current_team) < self.TEAM_SIZE:
            state = self._get_state(opponent_ids, current_team)
            state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            with torch.no_grad():
                q_values = self.dqn(state_t).cpu().numpy()[0]
            for a in range(self.action_dim):
                pid = self.action_to_id[a]
                if pid in opponent_ids or pid in current_team:
                    q_values[a] = -np.inf
            action = np.argmax(q_values)
            current_team.append(self.action_to_id[action])
        self.dqn.train()
        return current_team

    def save(self, path):
        torch.save(self.dqn.state_dict(), path)

    def load(self, path):
        self.dqn.load_state_dict(torch.load(path, map_location=self.device, weights_only=True))
        self.target_dqn.load_state_dict(self.dqn.state_dict())