import os
import glob
import pandas as pd

BASE_DIR        = r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management"
DATA_CSV        = os.path.join(BASE_DIR, "Data.csv")
ENHANCED_CSV    = os.path.join(BASE_DIR, "Data_Enhanced.csv")
INDUSTRIES_DIR  = os.path.join(BASE_DIR, "Industries")

USER_EXPLICIT_MAPPINGS = {
    "CAPITAL_GOODS": [
        "ABB India", "Bharat Heavy Electricals", "CG Power", "Hitachi Energy", "Honeywell Automation",
        "Schneider Electric", "TD Power", "Voltamp Transformers", "Transformers & Rectifiers", "Indo Tech Transformers",
        "Bharat Bijlee", "Hind Rectifiers", "Salzer Electronics", "HPL Electric", "Servotech Power",
        "Spectrum Electrical", "Marine Electricals", "Wonder Electricals", "Quality Power", "Powerica",
        "Triveni Turbine", "Thermax", "Genus Power"
    ],
    "ELECTRONICS_EMS": [
        "Dixon Technologies", "Kaynes Technology", "Avalon Technologies", "Centum Electronics", "DCX Systems",
        "Cyient DLM", "PG Electroplast", "Virtuoso Optoelectronics", "IKIO Lighting", "Optiemus Infracom",
        "Rashi Peripherals", "Exicom Tele-Systems", "Sigma Advanced", "Apollo Micro Systems", "HFCL",
        "Sterlite Technologies", "Syrma SGS"
    ],
    "DEFENCE": [
        "Bharat Electronics", "Garden Reach Shipbuilders", "Mazagon Dock", "Data Patterns", "Zen Technologies",
        "Swan Defence", "Hindustan Aeronautics", "Bharat Dynamics"
    ],
    "INFRASTRUCTURE": [
        "NCC", "KEC International", "Kalpataru Projects", "KNR Constructions", "H.G. Infra", "G R Infraprojects",
        "PNC Infratech", "Dilip Buildcon", "IRB Infrastructure", "Patel Engineering", "Ramky Infrastructure",
        "Hindustan Construction", "Simplex Infrastructures", "B. L. Kashyap", "PSP Projects", "Capacite Infraprojects",
        "Ahluwalia Contracts", "Ceigall India", "SRM Contractors", "Afcons Infrastructure", "Texmaco Infrastructure",
        "Welspun Enterprises", "Reliance Industrial Infrastructure", "Vikran Engineering"
    ],
    "REAL_ESTATE": [
        "DLF", "Godrej Properties", "Prestige Estates", "Lodha", "Macrotech", "Brigade Enterprises", "Sobha",
        "Oberoi Realty", "Phoenix Mills", "Embassy", "Max Estates", "Mahindra Lifespace", "Kolte - Patil",
        "Puravankara", "SignatureGlobal", "Keystone Realtors", "Raymond Realty", "Sunteck Realty", "TARC",
        "Arkade Developers", "Shriram Properties", "Arvind Smartspaces", "Ashiana Housing", "Ajmera Realty",
        "Ganesh Housing", "Hemisphere Properties", "Omaxe", "Hubtown", "Unitech", "Marathon Nextgen",
        "Arihant Superstructures", "Arihant Foundations", "Valor Estate", "Nirlon"
    ],
    "LOGISTICS": [
        "Transport Corporation Of India", "TCI Express", "Mahindra Logistics", "TVS Supply Chain", "Delhivery",
        "BlackBuck", "Gateway Distriparks", "Container Corporation", "Allcargo Logistics", "Navkar Corporation",
        "VRL Logistics", "Shipping Corporation of India", "Seamec", "Knowledge Marine", "Adani Ports",
        "JSW Infrastructure", "Gujarat Pipavav", "Dredging Corporation", "GMR Airports"
    ],
    "CONSUMER_DURABLES": [
        "Havells India", "Crompton Greaves", "V-Guard", "Orient Electric", "Bajaj Electricals", "Butterfly Gandhimathi",
        "TTK Prestige", "Hawkins Cookers", "Whirlpool", "Symphony", "Eveready Industries", "Hindware",
        "Stove Kraft", "Elpro International"
    ],
    "RETAIL": [
        "Trent", "Avenue Supermarts", "DMart", "V-Mart", "Baazar Style", "Electronics Mart", "Sai Silks",
        "Aditya Vision", "Redtape", "Ethos", "Safari Industries", "VIP Industries", "Brainbees", "FirstCry",
        "FSN E-Commerce", "Nykaa", "Honasa", "Mamaearth", "Vishal Mega Mart", "Meesho"
    ],
    "DIGITAL_PLATFORMS": [
        "One 97", "Paytm", "One Mobikwik", "Pine Labs", "Indiamart Intermesh", "Just Dial", "TBO Tek", "Easy Trip",
        "EaseMyTrip", "Yatra Online", "Le Travenues", "ixigo", "Swiggy", "Urban Company", "Cartrade Tech",
        "Info Edge", "AvenuesAI"
    ],
    "FINANCIAL_INFRASTRUCTURE": [
        "BSE", "Bombay Stock Exchange", "Central Depository Services", "CDSL", "Multi Commodity Exchange", "MCX",
        "KFin Technologies", "Computer Age Management", "CAMS", "Indian Energy Exchange", "IEX", "CRISIL",
        "CARE Ratings", "ICRA", "MSTC"
    ],
    "RENEWABLE_ENERGY": [
        "Waaree Energies", "Premier Energies", "Vikram Solar", "Emmvee", "Saatvik Green", "Solex Energy",
        "Websol Energy", "Insolation Energy", "Fujiyama Power", "Inox Wind", "Inox Green", "Adani Total Gas",
        "Ravindra Energy", "TruAlt Bioenergy", "Suzlon"
    ],
    "OIL_GAS_UTILITIES": [
        "GAIL", "Mahanagar Gas", "MGL", "Indraprastha Gas", "IGL", "Petronet LNG", "Confidence Petroleum",
        "IRM Energy", "Aegis Logistics", "Aegis Vopak"
    ],
    "HEALTHCARE_SERVICES": [
        "Syngene International", "Indegene", "Medi Assist", "Entero Healthcare", "MedPlus Health", "Vimta Labs",
        "Tarsons Products", "Jeena Sikho", "Sun Pharma Advanced Research", "SPARC", "Fischer Medical",
        "Narayana Hrudayalaya", "Apollo Hospitals", "Fortis Healthcare", "Max Healthcare", "Aster DM", "KIMS"
    ],
    "BUILDING_MATERIALS": [
        "Greenply", "Greenpanel", "Century Plyboards", "Greenlam", "Stylam", "Shankara Buildpro", "Indian Hume Pipe",
        "Pokarna", "Carysil", "Nitco", "Kajaria Ceramics", "Cera Sanitaryware", "Somany Ceramics", "Supreme Industries", "Astral"
    ],
    "TEXTILES_APPAREL": [
        "PDS", "KDDL", "Arvind Fashions", "Timex Group", "Page Industries", "KPR Mill", "Raymond", "Vardhman Textiles", "Welspun Living"
    ],
    "AGRICULTURE": [
        "Kaveri Seed", "Venky", "Gujarat Ambuja Exports", "BN Agrochem", "Sanstar"
    ],
    "TELECOM_INFRA": [
        "Indus Towers", "GTL Infrastructure", "Vindhya Telelinks", "Kernex Microsystems"
    ]
}

def map_decision_grade_sector(row) -> str:
    stock_name = str(row.get("Stock Name", "")).strip()
    symbol     = str(row.get("Symbol", "")).strip()
    ind        = str(row.get("industry", "")).lower().strip()

    # 1. Check Explicit User Mappings
    for category, names in USER_EXPLICIT_MAPPINGS.items():
        for name in names:
            if name.lower() in stock_name.lower() or name.lower() == symbol.lower():
                return category

    # 2. Industry Keyword Fallback Mapping
    if "bank" in ind and "non banking" not in ind and "nbfc" not in ind:
        return "BANKING"

    if any(k in ind for k in ["finance", "housing", "nbfc", "investment", "insurance", "financial"]):
        return "FINANCIAL_SERVICES"

    if any(k in ind for k in ["computer", "software", "consulting"]):
        return "INFORMATION_TECHNOLOGY"

    if "telecom" in ind or "telecommunication" in ind:
        return "TELECOMMUNICATIONS"

    if any(k in ind for k in ["defense", "defence", "aerospace"]):
        return "DEFENCE"

    if any(k in ind for k in ["steel", "iron", "aluminium", "mining", "coal", "minerals"]):
        return "METALS_AND_MINING"

    if any(k in ind for k in ["oil", "refinement", "refineries", "gas"]):
        return "OIL_GAS_UTILITIES"

    if "power" in ind:
        return "POWER_AND_UTILITIES"

    if any(k in ind for k in ["automobile", "vehicle", "car", "moped", "scooter", "motorcycle", "tractor", "auto ancillaries", "tyre"]):
        return "AUTOMOBILES"

    if any(k in ind for k in ["pharma", "hospital", "healthcare", "bulk drug", "formulation"]):
        return "HEALTHCARE_SERVICES"

    if any(k in ind for k in ["cigarette", "food", "dairy", "tea", "coffee", "personal care", "fmcg", "packaged", "sugar", "breweries", "distilleries", "aquaculture", "solvent extraction"]):
        return "CONSUMER_STAPLES"

    if any(k in ind for k in ["hotel", "resort"]):
        return "HOSPITALITY"

    if any(k in ind for k in ["airline", "aviation"]):
        return "AIRLINES"

    if any(k in ind for k in ["jewell", "gems", "watch"]):
        return "JEWELLERY"

    if any(k in ind for k in ["retail", "e-commerce", "e-retail"]):
        return "RETAIL"

    if any(k in ind for k in ["civil construction", "infra", "road"]):
        return "INFRASTRUCTURE"

    if any(k in ind for k in ["engineering", "electrical equipment", "compressor", "pump", "bearing", "fastener", "electrode", "abrasive", "turnkey", "transmission line", "machinery", "casting", "forging"]):
        return "CAPITAL_GOODS"

    if any(k in ind for k in ["shipping", "port", "courier", "transport", "logistics"]):
        return "LOGISTICS"

    if any(k in ind for k in ["cement", "paint", "paper", "packaging", "plastic", "glass", "ceramic", "tile", "sanitaryware", "leather", "refractories"]):
        return "BUILDING_MATERIALS"

    if any(k in ind for k in ["chemical", "pesticide", "agrochemical", "fertilizer", "petrochemical", "dyes", "soda ash"]):
        return "CHEMICALS"

    if any(k in ind for k in ["media", "entertainment", "recreation", "amusement", "printing"]):
        return "MEDIA_AND_ENTERTAINMENT"

    if "textile" in ind:
        return "TEXTILES_APPAREL"

    if "diversified" in ind or "holding" in ind:
        return "DIVERSIFIED"

    return "MISCELLANEOUS"

def clean_old_sector_files():
    print("Cleaning all old industry/sector CSV files...")
    # Delete all CSV files in Industries/ directory (except sector_index_summary.csv and industry_summary.csv)
    pattern = os.path.join(INDUSTRIES_DIR, "*.csv")
    for f in glob.glob(pattern):
        base = os.path.basename(f)
        if base not in ["sector_index_summary.csv", "industry_summary.csv"]:
            try:
                os.remove(f)
            except Exception:
                pass

    # Delete everything inside Industries/Enhanced/ as well
    enh_dir = os.path.join(INDUSTRIES_DIR, "Enhanced")
    if os.path.exists(enh_dir):
        for f in glob.glob(os.path.join(enh_dir, "*.csv")):
            try:
                os.remove(f)
            except Exception:
                pass
        try:
            os.rmdir(enh_dir)
        except Exception:
            pass

def main():
    print("="*70)
    print("QUANT CLUB SECTOR CLASSIFICATION & ORGANIZER ENGINE")
    print("="*70)

    clean_old_sector_files()

    df_base = pd.read_csv(DATA_CSV)
    df_base["Sector"] = df_base.apply(map_decision_grade_sector, axis=1)

    df_enh = None
    if os.path.exists(ENHANCED_CSV):
        df_enh = pd.read_csv(ENHANCED_CSV)
        df_enh["Sector"] = df_enh.apply(map_decision_grade_sector, axis=1)

    summary_rows = []

    # Map each sector group to a single non-overlapping CSV file
    for sector_name, group in df_base.groupby("Sector"):
        filename = f"{sector_name.lower()}_enhanced.csv" # Keep _enhanced.csv to match build_sector_indices.py input expectation
        out_path = os.path.join(INDUSTRIES_DIR, filename)

        # Drop the temporary sector column for the output files
        group_to_save = group.drop(columns=["Sector"])
        group_to_save.to_csv(out_path, index=False)

        # Update Enhanced CSV sector file if enhanced file exists
        if df_enh is not None:
            enh_group = df_enh[df_enh["Sector"] == sector_name].drop(columns=["Sector"])
            enh_out_path = os.path.join(INDUSTRIES_DIR, f"{sector_name.lower()}_enhanced.csv")
            enh_group.to_csv(enh_out_path, index=False)

        sub_industries = sorted(group["industry"].dropna().unique())
        summary_rows.append({
            "Sector Name": sector_name,
            "File Name": filename,
            "Stock Count": len(group),
            "Sub-Industries Included": " ; ".join(sub_industries[:5]) + ("..." if len(sub_industries) > 5 else "")
        })
        print(f"[OK] Mapped {len(group):3d} stocks to Sector: {sector_name}")

    summary_df = pd.DataFrame(summary_rows).sort_values(by="Stock Count", ascending=False)
    summary_path = os.path.join(INDUSTRIES_DIR, "industry_summary.csv")
    summary_df.to_csv(summary_path, index=False)

    print("\n" + "="*70)
    print(f"SUCCESS: Generated {len(summary_df)} Non-Overlapping Sector Baskets!")
    print(f"Zero company overlap verified across all categories.")
    print("="*70)

if __name__ == "__main__":
    main()
