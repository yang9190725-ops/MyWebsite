import re
import json
import time
import os
import sys
import requests
from datetime import datetime, timezone

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

OPGG_BASE = "https://www.op.gg"
DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPION_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json"
DDRAGON_ITEM_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/item.json"

REQUEST_DELAY = 1.5

session = requests.Session()
session.headers.update(HEADERS)

proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
if proxy:
    session.proxies = {"http": proxy, "https": proxy}


def fetch(url):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def unescape_rsc(html):
    return html.replace('\\"', '"')


def get_ddragon_version():
    versions = json.loads(fetch(DDRAGON_VERSIONS_URL))
    return versions[0]


def get_chinese_champion_data(version):
    url = DDRAGON_CHAMPION_URL.format(version=version)
    data = json.loads(fetch(url))
    result = {}
    for champ in data["data"].values():
        key = champ["id"].lower()
        result[key] = {"name_cn": champ["name"], "title": champ["title"]}
        result[champ["key"]] = {"name_cn": champ["name"], "title": champ["title"]}
    return result


def get_item_names_cn(version):
    url = DDRAGON_ITEM_URL.format(version=version)
    data = json.loads(fetch(url))
    result = {}
    for item_id, item in data["data"].items():
        result[int(item_id)] = item["name"]
    return result


def parse_champion_list(html):
    clean = unescape_rsc(html)
    pattern = (
        r'"key":"([^"]+)","name":"([^"]+)","image_url":"([^"]+)",'
        r'"id":(\d+),"is_rotation":\w+,"is_rip":\w+,'
        r'"win_rate":([0-9.]+),"pick_rate":([0-9.]+),"tier":(\d+),"rank":(\d+)'
    )
    matches = re.findall(pattern, clean)
    champions = []
    seen = set()
    for m in matches:
        key = m[0]
        if key in seen:
            continue
        seen.add(key)
        champions.append({
            "key": key,
            "name": m[1],
            "image_url": m[2],
            "id": int(m[3]),
            "win_rate": float(m[4]),
            "pick_rate": float(m[5]),
            "tier": int(m[6]),
            "rank": int(m[7]),
        })
    return champions


def extract_items_from_section(text, version, item_names_cn):
    items = []
    seen = set()
    for m in re.finditer(r'/item/(\d+)\.png', text):
        item_id = int(m.group(1))
        if item_id not in seen:
            seen.add(item_id)
            img_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"
            cn_name = item_names_cn.get(item_id, str(item_id))
            items.append({"id": item_id, "name_cn": cn_name, "image_url": img_url})
    return items


def parse_build_page(html, version, item_names_cn):
    clean = unescape_rsc(html)
    build = {"starter_items": [], "boots": [], "core_items": []}

    starter_idx = max(clean.find("starter_items_0"), clean.find("Starter Items"), -1)
    boots_idx = max(clean.find("boots_0"), clean.find("Boots Table"), -1)
    core_idx = max(clean.find("core_items_0"), clean.find("Core Builds"), -1)

    if starter_idx > 0:
        end = boots_idx if boots_idx > starter_idx else starter_idx + 3000
        build["starter_items"] = extract_items_from_section(clean[starter_idx:end], version, item_names_cn)[:3]

    if boots_idx > 0:
        end = core_idx if core_idx > boots_idx else boots_idx + 3000
        build["boots"] = extract_items_from_section(clean[boots_idx:end], version, item_names_cn)[:2]

    if core_idx > 0:
        core_section = clean[core_idx:core_idx + 5000]
        items = extract_items_from_section(core_section, version, item_names_cn)
        build["core_items"] = items[:6]

    return build


def parse_augments_page_cn(html):
    clean = unescape_rsc(html)
    augments = []
    aug_pattern = r'alt="([^"]+)"[^>]*src="([^"]*aram-augment/[^"]+)"'
    matches = re.findall(aug_pattern, clean)
    seen = set()
    for name, img_url in matches:
        name_clean = name.replace("&#x27;", "'").replace("&amp;", "&")
        if name_clean not in seen:
            seen.add(name_clean)
            img_clean = img_url.split("?")[0]
            tier = detect_augment_tier(clean, name)
            augments.append({
                "name_cn": name_clean,
                "image_url": img_clean,
                "tier": tier,
            })
    return augments


def parse_augments_page_en(html):
    clean = unescape_rsc(html)
    augments = []
    aug_pattern = r'alt="([^"]+)"[^>]*src="([^"]*aram-augment/[^"]+)"'
    matches = re.findall(aug_pattern, clean)
    seen = set()
    for name, img_url in matches:
        name_clean = name.replace("&#x27;", "'").replace("&amp;", "&")
        if name_clean not in seen:
            seen.add(name_clean)
            img_clean = img_url.split("?")[0]
            tier = detect_augment_tier(clean, name)
            augments.append({
                "name": name_clean,
                "image_url": img_clean,
                "tier": tier,
            })
    return augments


def detect_augment_tier(html, aug_name):
    idx = html.find(f'alt="{aug_name}"')
    if idx < 0:
        return "Silver"
    section = html[idx:idx + 800]
    if "prismatic" in section.lower() or "Prism" in section:
        return "Prismatic"
    gold_svg = "paint0_linear_7_1009"
    if gold_svg in section:
        return "Gold"
    silver_svg = "paint0_linear_7_1005"
    if silver_svg in section:
        return "Silver"
    return "Silver"


def main():
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "aram_builds.json")

    print("Fetching DDragon version...")
    version = get_ddragon_version()
    print(f"  Latest version: {version}")
    patch = ".".join(version.split(".")[:2])

    print("Fetching Chinese champion data...")
    cn_data = get_chinese_champion_data(version)
    print(f"  Got {len(cn_data)} mappings")

    print("Fetching Chinese item names...")
    item_names_cn = get_item_names_cn(version)
    print(f"  Got {len(item_names_cn)} item names")

    print("Fetching ARAM champion list...")
    aram_html = fetch(f"{OPGG_BASE}/lol/modes/aram")
    champions = parse_champion_list(aram_html)
    print(f"  Found {len(champions)} champions")

    if len(champions) < 50:
        print("ERROR: Too few champions found, something went wrong with parsing.")
        sys.exit(1)

    print("Fetching ARAM Mayhem champion list for augment availability...")
    time.sleep(REQUEST_DELAY)
    mayhem_html = fetch(f"{OPGG_BASE}/lol/modes/aram-mayhem")
    mayhem_champions = set()
    for m in re.findall(r'href="/lol/modes/aram-mayhem/([^/]+)/build"', mayhem_html):
        mayhem_champions.add(m)
    print(f"  {len(mayhem_champions)} champions have ARAM Mayhem data")

    total = len(champions)
    for i, champ in enumerate(champions):
        key = champ["key"]
        champ_name = champ["name"]
        champ_cn = cn_data.get(key.lower(), cn_data.get(key, {}))
        champ["name_cn"] = champ_cn.get("name_cn", champ_name)
        champ["title"] = champ_cn.get("title", "")
        champ["image_url"] = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champ_name}.png"

        print(f"[{i+1}/{total}] {champ_name} ({champ['name_cn']}/{champ['title']})...", end=" ", flush=True)

        try:
            time.sleep(REQUEST_DELAY)
            build_html = fetch(f"{OPGG_BASE}/lol/modes/aram/{key}/build")
            champ["build"] = parse_build_page(build_html, version, item_names_cn)
            items_count = len(champ["build"]["core_items"])
            print(f"build({items_count})", end=" ", flush=True)
        except Exception as e:
            print(f"build_err({e})", end=" ", flush=True)
            champ["build"] = {"starter_items": [], "boots": [], "core_items": []}

        if key in mayhem_champions:
            try:
                time.sleep(REQUEST_DELAY)
                # Fetch Chinese version for augment names
                cn_mayhem_html = fetch(f"{OPGG_BASE}/zh-cn/lol/modes/aram-mayhem/{key}/build")
                cn_augs = parse_augments_page_cn(cn_mayhem_html)

                time.sleep(REQUEST_DELAY)
                en_mayhem_html = fetch(f"{OPGG_BASE}/lol/modes/aram-mayhem/{key}/build")
                en_augs = parse_augments_page_en(en_mayhem_html)

                # Merge: use Chinese names with English fallback, match by image URL
                merged = []
                for en_aug in en_augs:
                    aug = dict(en_aug)
                    for cn_aug in cn_augs:
                        if cn_aug["image_url"] == en_aug["image_url"]:
                            aug["name_cn"] = cn_aug["name_cn"]
                            break
                    if "name_cn" not in aug:
                        aug["name_cn"] = aug["name"]
                    merged.append(aug)

                champ["augments"] = merged
                print(f"augs({len(merged)})")
            except Exception as e:
                print(f"aug_err({e})")
                champ["augments"] = []
        else:
            champ["augments"] = []
            print("no_mayhem")

    result = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "patch": patch,
        "champions": sorted(champions, key=lambda c: c["win_rate"], reverse=True),
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Saved {len(champions)} champions to {output_file}")
    print(f"Patch: {patch}, Date: {result['updated_at']}")


if __name__ == "__main__":
    main()
