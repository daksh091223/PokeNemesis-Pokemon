# PokéNemesis

PokéNemesis is a web application that helps you build a counter team (nemesis) against any opponent team of 6 Pokémon. It uses a Genetic Algorithm (or optionally a Deep Q-Network) to find the best 6 Pokémon to counter your opponent.

The frontend has a pixel-art theme, user accounts, team saving, and sprite images from Gen 5.

## Features

- User registration and login (session based)
- Team builder with 6 slots, searchable Pokémon list
- Save up to 10 teams per user
- Generate a nemesis (counter) team with one click
- Gen 5 pixel sprites served locally
- Two nemesis backends:
  - Genetic Algorithm (fast, no training)
  - Deep Q-Network (requires training)
- PostgreSQL database with complete Kanto Pokédex (up to generation 8, 905 Pokémon), types, moves, evolutions, and FireRed/LeafGreen move data

## Setup

### 1. Install dependencies

pip install -r requirements.txt


### 2. Set up PostgreSQL

Create a database named `PokemonDatabase`. Run `DB_Creation.sql` to create the tables.  
Update database credentials in `app.py` and `nemesis.py` if needed.

### 3. Populate the database

python DataExtraction.py

This fetches data from PokeAPI. It may take 10–20 minutes.

### 4. Download sprites

python SpriteTesting.py


Downloads Gen 5 sprites from PokéShowdown into `static/sprites/`.

### 5. Run the application

python app.py

Open `http://127.0.0.1:5000` in your browser.

## How the Nemesis Engine Works

A matchup matrix is precomputed for all 905 Pokémon.
The matrix is normalised to [0,1].

Fitness of a candidate team T against opponent O:


### Genetic Algorithm (default)

- Population: 50 random teams (6 Pokémon each, no duplicates, no opponent Pokémon)
- Selection: tournament (fitness-proportionate)
- Crossover: uniform with duplicate repair
- Mutation: replace one gene with a random valid Pokémon
- Elitism: keep best 2 individuals
- Runs for 100 generations per request (~0.5 seconds)

### Deep Q-Network (optional)

- State: opponent matchup scores + selected mask + team size one-hot
- Action: choose one of the top 200 Pokémon by base stat total
- Reward: improvement in team score (dense) plus final score bonus
- Training: 10,000+ episodes, epsilon-greedy, experience replay

To use DQN, train it first by calling `nemesis.train(episodes=15000)` and ensure `nemesis_dqn.pt` exists.
