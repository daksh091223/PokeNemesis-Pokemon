import requests
import psycopg2
import time
import urllib3
from tqdm import tqdm

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def connect_db():
    conn = psycopg2.connect(
        dbname="PokemonDatabase",
        user="postgres",
        password="Yash@1234",
        host="localhost",
        port="5432"
    )
    return conn, conn.cursor()

# ==========================================
# HELPER — retries up to 5 times with
# increasing wait if API returns empty/error
# ==========================================
def get(url, retries=5):
    for attempt in range(retries):
        try:
            resp = requests.get(url, verify=False, timeout=15)
            # 429 = rate limited — wait longer and retry
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print(f"\n⚠️  Rate limited. Waiting {wait}s before retry...")
                time.sleep(wait)
                continue
            # 403 = blocked — wait and retry
            if resp.status_code == 403:
                wait = (attempt + 1) * 5
                print(f"\n⚠️  403 Forbidden. Waiting {wait}s before retry...")
                time.sleep(wait)
                continue
            # Empty response — wait and retry
            if not resp.text.strip():
                wait = (attempt + 1) * 5
                print(f"\n⚠️  Empty response. Waiting {wait}s before retry...")
                time.sleep(wait)
                continue
            return resp
        except Exception as e:
            wait = (attempt + 1) * 5
            print(f"\n⚠️  Request error: {e}. Waiting {wait}s...")
            time.sleep(wait)
    print(f"\n❌ Failed after {retries} attempts: {url}")
    return None

# ==========================================
# STEP 1: Load Types & Type Effectiveness
# ==========================================
def load_types_and_effectiveness(cur):
    print("Fetching type data...")
    type_list_resp = get("https://pokeapi.co/api/v2/type/?limit=100")
    if type_list_resp is None:
        print("❌ Could not fetch type list. Check your internet connection.")
        return
    type_list_resp.raise_for_status()
    type_results = type_list_resp.json()["results"]

    types_data = []
    for t in tqdm(type_results, desc="Loading type details"):
        resp = get(t["url"])
        if resp is None:
            continue
        detail = resp.json()
        types_data.append({
            "id": detail["id"],
            "name": detail["name"],
            "damage_relations": detail["damage_relations"]
        })
        time.sleep(0.3)  # small delay between each type request

    print("Inserting types into database...")
    for t in types_data:
        cur.execute(
            "INSERT INTO types (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (t["id"], t["name"])
        )
    cur.connection.commit()

    print("Inserting type effectiveness...")
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM type_effectiveness")
    next_eff_id = cur.fetchone()[0] + 1

    for t in types_data:
        atk_id = t["id"]
        dr = t["damage_relations"]

        for rel in dr["double_damage_to"]:
            def_id = int(rel["url"].split("/")[-2])
            cur.execute("""
                INSERT INTO type_effectiveness (id, atk_id, def_id, multiplier)
                VALUES (%s, %s, %s, 2.0) ON CONFLICT (id) DO NOTHING
            """, (next_eff_id, atk_id, def_id))
            next_eff_id += 1

        for rel in dr["half_damage_to"]:
            def_id = int(rel["url"].split("/")[-2])
            cur.execute("""
                INSERT INTO type_effectiveness (id, atk_id, def_id, multiplier)
                VALUES (%s, %s, %s, 0.5) ON CONFLICT (id) DO NOTHING
            """, (next_eff_id, atk_id, def_id))
            next_eff_id += 1

        for rel in dr["no_damage_to"]:
            def_id = int(rel["url"].split("/")[-2])
            cur.execute("""
                INSERT INTO type_effectiveness (id, atk_id, def_id, multiplier)
                VALUES (%s, %s, %s, 0.0) ON CONFLICT (id) DO NOTHING
            """, (next_eff_id, atk_id, def_id))
            next_eff_id += 1

    cur.connection.commit()
    print("Types and effectiveness loaded successfully!")

# ==========================================
# STEP 2: Load Moves
# ==========================================
def load_moves(cur):
    print("Loading moves...")
    for i in tqdm(range(1, 201)):
        move_resp = get(f"https://pokeapi.co/api/v2/move/{i}/")
        if move_resp is None or move_resp.status_code != 200:
            continue
        move = move_resp.json()
        type_id = int(move["type"]["url"].split("/")[-2])
        cur.execute("""
            INSERT INTO moves (id, name, type_id, power, accuracy)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
        """, (i, move["name"], type_id, move.get("power") or 0, move.get("accuracy") or 100))
        time.sleep(0.1)
    cur.connection.commit()
    print("Moves loaded successfully!")

# ==========================================
# STEP 3: Load Pokemon
# ==========================================

# BUG FIX: original replace() chain was broken for generations 4-9
# e.g. "viii" → replace("i","1") first → "1111" before "viii" rule ever runs
# Fix: use a proper dictionary lookup instead
def roman_to_int(roman):
    mapping = {
        'i': 1, 'ii': 2, 'iii': 3, 'iv': 4,
        'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9
    }
    return mapping.get(roman, 1)

def load_pokemon(cur):
    print("Loading Pokemon...")
    count_resp = get("https://pokeapi.co/api/v2/pokemon?limit=1")
    if count_resp is None:
        print("❌ Could not fetch Pokemon count.")
        return
    total_pokemon = count_resp.json()["count"]
    print(f"Total Pokemon to load: {total_pokemon}")

    for i in tqdm(range(1, total_pokemon + 1)):
        poke_resp = get(f"https://pokeapi.co/api/v2/pokemon/{i}/")
        if poke_resp is None or poke_resp.status_code != 200:
            continue
        poke = poke_resp.json()
        stats = {s["stat"]["name"]: s["base_stat"] for s in poke["stats"]}

        species_url = poke["species"]["url"]
        species_resp = get(species_url)
        if species_resp is None or species_resp.status_code != 200:
            generation = None
        else:
            species_data = species_resp.json()
            gen_name = species_data["generation"]["name"]
            roman = gen_name.split("-")[-1]
            generation = roman_to_int(roman)

        cur.execute("""
            INSERT INTO pokemon (id, name, hp, atk, def, sp_atk, sp_def, speed, generation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
        """, (
            i, poke["name"],
            stats["hp"], stats["attack"], stats["defense"],
            stats["special-attack"], stats["special-defense"], stats["speed"],
            generation
        ))

        for t in poke["types"]:
            type_id = int(t["type"]["url"].split("/")[-2])
            cur.execute("""
                INSERT INTO pokemon_types (pokemon_id, type_id)
                VALUES (%s, %s) ON CONFLICT (pokemon_id, type_id) DO NOTHING
            """, (i, type_id))

        for move_entry in poke["moves"]:
            move_id = int(move_entry["move"]["url"].split("/")[-2])
            for version in move_entry["version_group_details"]:
                if version["version_group"]["name"] == "firered-leafgreen":
                    try:
                        cur.execute("SAVEPOINT move_insert")
                        cur.execute("""
                            INSERT INTO pokemon_moves (pokemon_id, move_id, level_learned)
                            VALUES (%s, %s, %s)
                        """, (i, move_id, version["level_learned_at"]))
                    except psycopg2.errors.ForeignKeyViolation:
                        # Roll back only this one move, not the whole pokemon
                        cur.execute("ROLLBACK TO SAVEPOINT move_insert")
                        continue

        cur.connection.commit()
        time.sleep(0.05)

    print("Pokemon loaded successfully!")

# ==========================================
# STEP 4: Load Evolutions
# ==========================================
def extract_evolution_links(chain_link, start_id=None):
    current_species = chain_link['species']
    current_id = int(current_species['url'].split('/')[-2])

    if start_id is not None:
        level = 40  # default for non-level evolutions
        for detail in chain_link.get('evolution_details', []):
            if detail.get('trigger', {}).get('name') == 'level-up' and detail.get('min_level') is not None:
                level = detail['min_level']
                break
        yield (start_id, current_id, level)

    for next_link in chain_link.get('evolves_to', []):
        yield from extract_evolution_links(next_link, current_id)

def load_evolutions(cur):
    print("Loading evolutions...")
    count_resp = get("https://pokeapi.co/api/v2/pokemon-species?limit=1")
    if count_resp is None:
        print("❌ Could not fetch species count.")
        return
    total_species = count_resp.json()["count"]
    print(f"Total species to process: {total_species}")

    for species_id in tqdm(range(1, total_species + 1)):
        species_url = f"https://pokeapi.co/api/v2/pokemon-species/{species_id}/"
        resp = get(species_url)
        if resp is None or resp.status_code != 200:
            continue
        species_data = resp.json()
        evo_chain_url = species_data.get('evolution_chain', {}).get('url')
        if not evo_chain_url:
            continue
        chain_resp = get(evo_chain_url)
        if chain_resp is None or chain_resp.status_code != 200:
            continue
        chain_data = chain_resp.json()

        for start_id, end_id, level in extract_evolution_links(chain_data['chain']):
            try:
                cur.execute("SAVEPOINT evo_insert")
                cur.execute("""
                    INSERT INTO evolutions (pokemon_id_start, pokemon_id_end, level_evolved)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (start_id, end_id, level))
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT evo_insert")
                continue

        cur.connection.commit()
        time.sleep(0.05)

    print("Evolutions loaded successfully!")

# ==========================================
# MAIN — uncomment one step at a time!
# ==========================================
def main():
    conn, cur = connect_db()
    try:
        #load_types_and_effectiveness(cur)   # STEP 1 — run first (~1 min)
        # load_moves(cur)                   # STEP 2 — run second (~2 mins)
        #load_pokemon(cur)                 # STEP 3 — run third (~20 mins)
        load_evolutions(cur)              # STEP 4 — run last (~10 mins)
        print("Done!")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()