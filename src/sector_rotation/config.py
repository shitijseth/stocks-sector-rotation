"""Paths, ETF curation, and shared constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("SECTOR_DATA_DIR", ROOT / "data"))
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = ROOT / "reports"

for d in (DATA_DIR, CACHE_DIR, REPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Optional API keys (read from environment / .env). Modules gracefully skip if absent.
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
FMP_API_KEY = os.environ.get("FMP_API_KEY")

# SEC EDGAR requires a contact User-Agent. Override via env if you publish.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "sector-rotation-research contact@example.com"
)

# Curated thematic ETFs. Each ETF's holdings (>= weight threshold) become a tag
# applied to the underlying tickers. Issuer is auto-detected by etf-scraper from
# the known fund universe; if a ticker isn't recognised, we fall back to yfinance.
#
# Format: ticker -> theme_label
THEMATIC_ETFS: dict[str, str] = {
    # Semiconductors / hardware
    "SOXX": "semiconductors",
    "SMH":  "semiconductors",
    "PSI":  "semiconductors",
    "XSD":  "semiconductors-equal-weight",
    # Software / cloud / internet
    "IGV":  "software",
    "CLOU": "cloud-computing",
    "SKYY": "cloud-computing",
    "WCLD": "saas",
    "FDN":  "internet",
    "PNQI": "internet",
    "SOCL": "social-media",
    # AI / robotics / automation
    "BOTZ": "robotics-ai",
    "ROBO": "robotics-ai",
    "AIQ":  "ai",
    "IRBO": "robotics-ai",
    # Cybersecurity
    "HACK": "cybersecurity",
    "CIBR": "cybersecurity",
    "BUG":  "cybersecurity",
    # Fintech / payments / blockchain
    "FINX": "fintech",
    "ARKF": "fintech",
    "IPAY": "payments",
    "BLOK": "blockchain",
    "BITQ": "crypto-equities",
    # E-commerce / digital consumer
    "IBUY": "online-retail",
    "ONLN": "online-retail",
    "EBIZ": "e-commerce",
    # Gaming / esports
    "ESPO": "gaming-esports",
    "GAMR": "gaming-esports",
    "HERO": "gaming-esports",
    # Connectivity / 5G / IoT
    "FIVG": "5g",
    "NXTG": "5g-connectivity",
    "SNSR": "iot",
    # 3D printing
    "PRNT": "3d-printing",
    # Autonomous / EV / batteries
    "DRIV": "autonomous-ev",
    "IDRV": "autonomous-ev",
    "KARS": "electric-vehicles",
    "LIT":  "lithium-battery",
    # Space / aerospace / defense
    "UFO":  "space",
    "ROKT": "space",
    "ARKX": "space-exploration",
    "ITA":  "aerospace-defense",
    "XAR":  "aerospace-defense",
    "PPA":  "defense",
    # Genomics / biotech / health innovation
    "ARKG": "genomics",
    "GNOM": "genomics",
    "IBB":  "biotech",
    "XBI":  "biotech-equal-weight",
    "IHI":  "medical-devices",
    "IHE":  "pharmaceuticals",
    "MJ":   "cannabis",
    # Energy transition
    "ICLN": "clean-energy",
    "TAN":  "solar",
    "FAN":  "wind",
    "HYDR": "hydrogen",
    "URA":  "uranium",
    "URNM": "uranium-miners",
    "NLR":  "nuclear",
    "PBW":  "clean-energy",
    # Traditional energy
    "XLE":  "energy-broad",
    "XOP":  "oil-gas-ep",
    "OIH":  "oil-services",
    "AMLP": "midstream-mlp",
    # Materials / mining
    "COPX": "copper-miners",
    "SIL":  "silver-miners",
    "GDX":  "gold-miners",
    "GDXJ": "gold-miners-junior",
    "REMX": "rare-earth-strategic-metals",
    "PICK": "metals-mining",
    # Water / agriculture
    "PHO":  "water",
    "FIW":  "water",
    "MOO":  "agribusiness",
    "VEGI": "agribusiness",
    # Real estate / homebuilders
    "VNQ":  "reits",
    "ITB":  "homebuilders",
    "XHB":  "homebuilders",
    # Financials slices
    "KBE":  "banks",
    "KRE":  "regional-banks",
    "KIE":  "insurance",
    # Consumer slices
    "XRT":  "retail",
    "PEJ":  "leisure-entertainment",
    "BETZ": "gambling",
    "AWAY": "travel",
    "JETS": "airlines",
    # Innovation umbrellas
    "ARKK": "ark-disruptive-innovation",
    "ARKQ": "ark-autonomous-robotics",
    "ARKW": "ark-next-gen-internet",
    # Infrastructure
    "PAVE": "infrastructure",
}

# Min weight (fraction, e.g. 0.005 = 0.5%) for an ETF holding to count as a tag.
# Below this we treat the position as noise (filler / passive index leftovers).
ETF_MIN_WEIGHT = 0.005

# Universe / data settings
LOOKBACK_YEARS = 5
MIN_AVG_DOLLAR_VOLUME = 1_000_000  # filter illiquid tickers
PRICE_BATCH_SIZE = 200
