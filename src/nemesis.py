import random
from typing import List, Dict, Tuple

# ---------------------------------------------------------
# TYPE DEFINITIONS
# ---------------------------------------------------------
class Move:
    def __init__(self, name: str, base_power: int, type: str, accuracy: int = 100):
        self.name = name
        self.base_power = base_power
        self.type = type
        self.accuracy = accuracy

class Pokemon:
    def __init__(self, id: int, name: str, types: List[str], base_stat_total: int, speed: int, level: int = 50, learnset: List['Move'] = None):
        self.id = id
        self.name = name
        self.types = types
        self.base_stat_total = base_stat_total
        self.speed = speed
        self.level = level
        self.learnset = learnset or []
        self.assigned_moves: List['Move'] = []

    def __repr__(self):
        return f"{self.name} (Lv.{self.level})"

    def __eq__(self, other):
        if not isinstance(other, Pokemon):
            return False
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

# ---------------------------------------------------------
# TYPE EFFECTIVENESS CHART
# Chart maps Attacking Type -> Defending Type -> Multiplier
# Default is 1.0. Only exceptions need to be strictly mapped.
# ---------------------------------------------------------
TYPE_CHART: Dict[str, Dict[str, float]] = {}

def load_type_chart_from_db(db_params: dict, table_name: str = 'type_effectiveness'):
    """
    Connects to PostgreSQL and loads the type effectiveness table into TYPE_CHART.
    Expects a table containing multiplier, opponent_type, and my_type (attacker).
    """
    global TYPE_CHART
    try:
        import psycopg2
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()
        
        # Using placeholder query names according to user spec (multiplier, types of opponent, my type)
        cursor.execute(f"SELECT my_type, opponent_type, multiplier FROM {table_name}")
        rows = cursor.fetchall()
        
        TYPE_CHART = {}
        for my_type, opponent_type, multiplier in rows:
            if my_type not in TYPE_CHART:
                TYPE_CHART[my_type] = {}
            TYPE_CHART[my_type][opponent_type] = float(multiplier)
            
        cursor.close()
        conn.close()
        print("Type chart successfully loaded from PostgreSQL database.")
    except Exception as e:
        print(f"Error loading type chart from PostgreSQL: {e}")

def get_type_multiplier(attack_type: str, defense_types: List[str]) -> float:
    """Calculates the combined multiplier of an attack against a suite of defense types."""
    multiplier = 1.0
    attack_matchups = TYPE_CHART.get(attack_type, {})
    for def_type in defense_types:
        multiplier *= attack_matchups.get(def_type, 1.0)
    return multiplier

# ---------------------------------------------------------
# MATCHUP SCORING ENGINE (Where Level/Moveset Constraints Apply)
# ---------------------------------------------------------
def evaluate_move_against_opponent(move: Move, candidate: Pokemon, opponent: Pokemon, effective_level: int) -> float:
    """Calculates the expected damage of a single move against an opponent."""
    # Base Damage scaled by level
    damage = move.base_power * (effective_level / 50.0)
    
    # STAB (Same Type Attack Bonus)
    if move.type in candidate.types:
        damage *= 1.5
        
    # Type Effectiveness
    multiplier = get_type_multiplier(move.type, opponent.types)
    damage *= multiplier
    
    # Accuracy factored as an expected value
    expected_damage = damage * (move.accuracy / 100.0)
    
    return expected_damage

def assign_optimal_moveset(candidate: Pokemon, opponent_team: List[Pokemon], max_allowed_level: int):
    """Selects the 4 best moves from a Pokemon's learnset against the specific opponent team."""
    effective_level = min(candidate.level, max_allowed_level)
    
    move_scores = {}
    for move in candidate.learnset:
        total_score = 0.0
        # Evaluate how good this move is across all opponents
        for opponent in opponent_team:
            total_score += evaluate_move_against_opponent(move, candidate, opponent, effective_level)
            
        # Keep track of the highest scoring moves by name (to avoid duplicate moves)
        if move.name not in move_scores or total_score > move_scores[move.name][0]:
            move_scores[move.name] = (total_score, move)
            
    # Sort moves by their total usefulness score and take the top 4
    sorted_moves = sorted(move_scores.values(), key=lambda x: x[0], reverse=True)
    candidate.assigned_moves = [item[1] for item in sorted_moves[:4]]

def f_matchup_score(candidate: Pokemon, opponent: Pokemon, max_allowed_level: int) -> float:
    """
    f(t, o) function to calculate individual matchup score.
    Applies Level limits and Moveset optimizations.
    """
    # 1. Level Constraint: Candidate level is capped at max_allowed_level
    effective_level = min(candidate.level, max_allowed_level)
    
    # 2. Moveset Optimization: Find the best move against this specific opponent
    best_expected_damage = 0.0
    for move in candidate.assigned_moves:
        # Calculate expected damage based on constraints
        simulated_damage = evaluate_move_against_opponent(move, candidate, opponent, effective_level)
        if simulated_damage > best_expected_damage:
            best_expected_damage = simulated_damage
            
    # 3. Defensive resilience / Speed factor...
    speed_multiplier = 1.2 if candidate.speed > opponent.speed else 1.0
    
    return best_expected_damage * speed_multiplier

# Precomputed Matchup Matrix: matchup[candidate_id][opponent_id] = float_score
MATCHUP_MATRIX: Dict[int, Dict[int, float]] = {}

def precalculate_matchups(all_pokemon: List[Pokemon], opponent_team: List[Pokemon]) -> List[Pokemon]:
    """
    Populates the MATCHUP_MATRIX using the f_matchup_score function.
    """
    global MATCHUP_MATRIX
    
    # Calculate constraints based on the opponent's team
    max_allowed_level = 50
    max_opponent_bst = float('inf')
    
    if opponent_team:
        max_allowed_level = max(p.level for p in opponent_team)
        max_opponent_bst = max(p.base_stat_total for p in opponent_team)
        
    opponent_names = {p.name for p in opponent_team}
    valid_pokemon = []
    
    for candidate in all_pokemon:
        # Guarantee 12 completely distinct Pokemon (6 vs 6 without mirror matches)
        if candidate.name in opponent_names:
            continue
            
        # Impose cap: candidate's base stat total must not exceed the highest of the opponent's team
        if candidate.base_stat_total > max_opponent_bst:
            continue
            
        valid_pokemon.append(candidate)
        
        # Calculate optimal learnset allocation
        assign_optimal_moveset(candidate, opponent_team, max_allowed_level)
        
        MATCHUP_MATRIX[candidate.id] = {}
        for opponent in opponent_team:
            MATCHUP_MATRIX[candidate.id][opponent.id] = f_matchup_score(candidate, opponent, max_allowed_level)
            
    return valid_pokemon

# ---------------------------------------------------------
# 1. FITNESS FUNCTION (Scoring and Penalties)
# ---------------------------------------------------------
def base_score(team: List[Pokemon], opponent_team: List[Pokemon]) -> float:
    """Calculates the cumulative offensive coverage score of the entire team."""
    total = 0.0
    for o in opponent_team:
        # Sum the matchup scores of all our team members against this specific opponent
        team_sum = sum(MATCHUP_MATRIX[t.id][o.id] for t in team)
        total += team_sum
    return total

def too_many_same_types(team: List[Pokemon]) -> bool:
    """Heuristic: Flag if a team has too many overlapping types."""
    type_counts = {}
    for p in team:
        for t in p.types:
            type_counts[t] = type_counts.get(t, 0) + 1
            if type_counts[t] >= 3:
                return True
    return False

def has_fast(team: List[Pokemon]) -> bool:
    """Heuristic: Ensure the team has at least one fast Pokemon."""
    return any(p.speed >= 100 for p in team)

def fitness(team: List[Pokemon], opponent_team: List[Pokemon]) -> float:
    """The complete fitness function combining base score and penalties."""
    score = base_score(team, opponent_team)

    # Apply penalties
    if too_many_same_types(team):
        score -= 10.0

    if not has_fast(team):
        score -= 5.0

    # Reward Pokemon with higher Base Stats (incentivize closing the gap to the max cap)
    avg_bst = sum(p.base_stat_total for p in team) / len(team)
    score += (avg_bst / 20.0) # Scaled so it rewards good stats without entirely ignoring matchup value

    return score

# ---------------------------------------------------------
# 2. INITIALIZE POPULATION
# ---------------------------------------------------------
def init_population(pop_size: int, all_pokemon: List[Pokemon]) -> List[List[Pokemon]]:
    """Generates the initial random population of teams."""
    population = []
    for _ in range(pop_size):
        team = random.sample(all_pokemon, 6)
        population.append(team)
    return population

# ---------------------------------------------------------
# 3. SELECTION (Tournament)
# ---------------------------------------------------------
def tournament_selection(population: List[List[Pokemon]], fitnesses: List[float], k: int = 3) -> List[Pokemon]:
    """Selects the best team from a random subset of the population."""
    # Combine team and its fitness score
    pop_with_fitness = list(zip(population, fitnesses))
    # Randomly select 'k' individuals for the tournament
    selected = random.sample(pop_with_fitness, k)
    # Sort the tournament participants by fitness (descending)
    selected.sort(key=lambda x: x[1], reverse=True)
    # Return the team (index 0) of the winner
    return selected[0][0]

# ---------------------------------------------------------
# 4. CROSSOVER
# ---------------------------------------------------------
def crossover(parent1: List[Pokemon], parent2: List[Pokemon]) -> List[Pokemon]:
    """Combines two parent teams to create a child team."""
    # Take first half of parent1 and second half of parent2
    child = parent1[:3] + parent2[3:]
    
    # Remove duplicates while preserving order
    child_unique = []
    for p in child:
        if p not in child_unique:
            child_unique.append(p)
    child = child_unique
    
    # Fill missing slots if duplicates were removed
    pool = parent1 + parent2
    while len(child) < 6:
        candidate = random.choice(pool)
        if candidate not in child:
            child.append(candidate)
            
    return child

# ---------------------------------------------------------
# 5. MUTATION
# ---------------------------------------------------------
def mutate(team: List[Pokemon], all_pokemon: List[Pokemon], mutation_rate: float = 0.2) -> List[Pokemon]:
    """Randomly swaps out a Pokemon in the team to maintain genetic diversity."""
    if random.random() < mutation_rate:
        idx = random.randint(0, 5)
        new_pokemon = random.choice(all_pokemon)
        
        if new_pokemon not in team:
            team[idx] = new_pokemon
            
    return team

# ---------------------------------------------------------
# 6. MAIN GA LOOP
# ---------------------------------------------------------
def genetic_algorithm(
    opponent_team: List[Pokemon], 
    all_pokemon: List[Pokemon], 
    generations: int = 100, 
    pop_size: int = 50,
    mutation_rate: float = 0.2
) -> List[Pokemon]:
    """
    Runs the Genetic Algorithm to find the optimal team.
    Returns the single best team.
    """
    # 0. Precalculate matchup matrix and filter out candidates exceeding the BST cap
    available_pokemon = precalculate_matchups(all_pokemon, opponent_team)
    
    if len(available_pokemon) < 6:
        raise ValueError("Not enough candidates available under the opponent's Base Stat cap to form a 6-Pokemon team.")

    # 1. Initialize
    population = init_population(pop_size, available_pokemon)
    
    # Track the global best to ensure it's never lost
    global_best_team = None
    global_best_score = float('-inf')

    for gen in range(generations):
        # Calculate fitness for the current population
        fitnesses = [fitness(team, opponent_team) for team in population]
        
        # Elitism: find the best team in the current generation
        current_best_idx = fitnesses.index(max(fitnesses))
        current_best_team = population[current_best_idx]
        current_best_score = fitnesses[current_best_idx]
        
        if current_best_score > global_best_score:
            global_best_score = current_best_score
            global_best_team = current_best_team

        # Generate new population
        new_population = []
        
        # Ensure the absolute best team survives to the next generation (Elitism)
        new_population.append(list(current_best_team))

        # Fill the rest of the new generation
        while len(new_population) < pop_size:
            p1 = tournament_selection(population, fitnesses)
            p2 = tournament_selection(population, fitnesses)
            
            child = crossover(p1, p2)
            child = mutate(child, available_pokemon, mutation_rate)
            
            new_population.append(child)
            
        population = new_population

        # Optional progress tracking
        if gen % 10 == 0 or gen == generations - 1:
            print(f"Gen {gen}: Best Score = {current_best_score:.2f}")

    # After all generations, return the single best team
    final_fitnesses = [fitness(team, opponent_team) for team in population]
    best_idx = final_fitnesses.index(max(final_fitnesses))
    return population[best_idx]

# ---------------------------------------------------------
# 7. SEQUENTIAL GENERATOR (DISJOINT TEAMS)
# ---------------------------------------------------------
def get_multiple_disjoint_teams(
    opponent_team: List[Pokemon], 
    all_pokemon: List[Pokemon],
    num_teams: int = 2,
    generations: int = 100, 
    pop_size: int = 50,
    mutation_rate: float = 0.2
) -> List[List[Pokemon]]:
    """
    Runs the Genetic Algorithm iteratively, banning previously chosen Pokemon,
    to ensure the resulting teams are completely disjoint.
    """
    disjoint_teams = []
    available_pokemon = list(all_pokemon)
    
    for _ in range(num_teams):
        if len(available_pokemon) < 6:
            break
            
        # Get the single best team from a fresh GA run
        best_team = genetic_algorithm(
            opponent_team, available_pokemon, 
            generations, pop_size, mutation_rate
        )
        
        if not best_team:
            break
            
        disjoint_teams.append(best_team)
        
        # Remove these Pokemon from the pool for the next run
        banned_ids = set(p.id for p in best_team)
        available_pokemon = [p for p in available_pokemon if p.id not in banned_ids]
        
    return disjoint_teams