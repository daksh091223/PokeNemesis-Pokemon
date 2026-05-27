
-- Drop tables if they exist (for clean re-run)
DROP TABLE IF EXISTS teams CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS evolutions CASCADE;
DROP TABLE IF EXISTS pokemon_moves CASCADE;
DROP TABLE IF EXISTS moves CASCADE;
DROP TABLE IF EXISTS type_effectiveness CASCADE;
DROP TABLE IF EXISTS pokemon_types CASCADE;
DROP TABLE IF EXISTS types CASCADE;
DROP TABLE IF EXISTS pokemon CASCADE;

CREATE TABLE pokemon (
    id INT PRIMARY KEY,
    name VARCHAR(255),
    hp INT,
    atk INT,
    def INT,
    sp_atk INT,
    sp_def INT,
    speed INT,
    generation INT
);

CREATE TABLE types (
    id INT PRIMARY KEY,
    name VARCHAR(255)
);

CREATE TABLE pokemon_types (
    pokemon_id INT,
    type_id INT,
    PRIMARY KEY (pokemon_id, type_id),
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (type_id) REFERENCES types(id)
);

CREATE TABLE type_effectiveness (
    id INT PRIMARY KEY,
    atk_id INT,
    def_id INT,
    multiplier FLOAT,
    FOREIGN KEY (atk_id) REFERENCES types(id),
    FOREIGN KEY (def_id) REFERENCES types(id)
);

CREATE TABLE moves (
    id INT PRIMARY KEY,
    name VARCHAR(255),
    type_id INT,
    power INT,
    accuracy INT,
    FOREIGN KEY (type_id) REFERENCES types(id)
);

CREATE TABLE pokemon_moves (
    pokemon_id INT,
    move_id INT,
    level_learned INT,
    FOREIGN KEY (pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (move_id) REFERENCES moves(id)
);

-- FIX 1: Added missing comma before FOREIGN KEY
-- FIX 2: Added PRIMARY KEY so ON CONFLICT DO NOTHING works correctly
CREATE TABLE evolutions (
    pokemon_id_start INT,
    pokemon_id_end INT,
    level_evolved INT,
    PRIMARY KEY (pokemon_id_start, pokemon_id_end),
    FOREIGN KEY (pokemon_id_start) REFERENCES pokemon(id),
    FOREIGN KEY (pokemon_id_end) REFERENCES pokemon(id)
);

-- FIX 3: id uses SERIAL (auto-increment) so inserts don't need to supply id
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL
);

-- FIX 4: id uses SERIAL (auto-increment)
CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    pokemon_ids INT[] NOT NULL
);