import requests
import psycopg2
import os
import urllib3
from tqdm import tqdm

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_SPRITE_URL = "https://play.pokemonshowdown.com/sprites/gen5"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# ---------- Database connection ----------
conn = psycopg2.connect(
    dbname="PokemonDatabase",
    user="postgres",
    password="abcd1234",
    host="localhost",
    port="5432"
)
cur = conn.cursor()

# ---------- Format name to match Showdown's file naming ----------
def format_showdown_name(name: str) -> str:
    """Strips special characters to match Showdown's file naming rules."""
    chars_to_remove = [" ", "-", ".", "'", ":", "♀", "♂"]
    formatted_name = name.lower()
    for char in chars_to_remove:
        formatted_name = formatted_name.replace(char, "")
    return formatted_name

# ---------- Download sprites ----------
def download_sprites(pokemon_names, save_folder="static/sprites"):
    """Downloads static sprites from Pokémon Showdown for all given names."""
    if not os.path.exists(save_folder):
        os.makedirs(save_folder)

    print(f"Downloading sprites to /{save_folder}...")
    failed = []

    for name in tqdm(pokemon_names):
        clean_name = format_showdown_name(name)
        url = f"{BASE_SPRITE_URL}/{clean_name}.png"
        response = requests.get(url, headers=HEADERS, verify=False)

        if response.status_code == 200:
            file_path = os.path.join(save_folder, f"{clean_name}.png")
            with open(file_path, 'wb') as f:
                f.write(response.content)
        else:
            failed.append((name, url))

    # Print summary
    if failed:
        print(f"\nFailed to download {len(failed)} sprites (HTTP {response.status_code}):")
        for name, url in failed[:10]:  # show first 10 for brevity
            print(f"  {name} -> {url}")
        if len(failed) > 10:
            print(f"  ... and {len(failed)-10} more.")
    else:
        print("\nAll sprites downloaded successfully!")

# ---------- Main ----------
if __name__ == "__main__":
    # Get all Pokémon names from the database
    cur.execute("SELECT name FROM pokemon ORDER BY id")
    all_names = [row[0] for row in cur.fetchall()]

    print(f"Found {len(all_names)} Pokémon in database.")
    download_sprites(all_names)

    cur.close()
    conn.close()