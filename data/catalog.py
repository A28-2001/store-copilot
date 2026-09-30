"""Fictional brands and products for the synthetic catalog.

Every brand here is made up. Local (South Florida flavored) brands are flagged
so the app can report local brand share.
"""

# brand -> is_local_brand
BRANDS = {
    # local
    "Biscayne": 1, "Palmetto": 1, "Mangrove": 1, "Seagrape": 1, "Coquina": 1,
    "Redland": 1, "Sawgrass": 1, "Wynwood": 1, "Poinciana": 1, "Tamiami": 1,
    # national (fictional)
    "Northfield": 0, "Wildroot": 0, "Meadowlark": 0, "Sunhaven": 0, "True Acre": 0,
    "Fernwood": 0, "Oakline": 0, "Stillwater": 0, "Goldenrod": 0, "Clearspring": 0,
    "Juniper Lane": 0, "Harbor Hill": 0, "Kindred Field": 0, "Bright Furrow": 0, "Aster & Oak": 0,
}

CATEGORY_BRANDS = {
    "Produce": ["Biscayne", "Redland", "Mangrove", "Sawgrass", "True Acre", "Sunhaven", "Kindred Field", "Bright Furrow"],
    "Meat & Seafood": ["Palmetto", "Coquina", "Tamiami", "Harbor Hill", "True Acre", "Kindred Field", "Stillwater"],
    "Dairy & Eggs": ["Sawgrass", "Redland", "Meadowlark", "Clearspring", "Goldenrod", "Kindred Field", "Oakline"],
    "Pantry": ["Wynwood", "Coquina", "Tamiami", "Biscayne", "Northfield", "True Acre", "Fernwood", "Oakline", "Stillwater", "Goldenrod", "Juniper Lane"],
    "Snacks": ["Wynwood", "Mangrove", "Poinciana", "Oakline", "Kindred Field", "Bright Furrow", "Harbor Hill", "Stillwater", "Northfield"],
    "Beverages & Juices": ["Seagrape", "Biscayne", "Coquina", "Wynwood", "Clearspring", "Stillwater", "Juniper Lane", "Goldenrod"],
    "Supplements": ["Wildroot", "Mangrove", "Sawgrass", "Clearspring", "Fernwood", "Juniper Lane", "True Acre", "Aster & Oak"],
    "Beauty & Body": ["Coquina", "Poinciana", "Seagrape", "Fernwood", "Aster & Oak", "Juniper Lane", "Oakline", "Goldenrod"],
}

# category -> [(product, [sizes])]
PRODUCTS = {
    "Produce": [
        ("Organic Hass Avocados", ["each", "4 ct"]), ("Heirloom Tomatoes", ["1 lb"]),
        ("Organic Baby Spinach", ["5 oz", "10 oz"]), ("Organic Lacinato Kale", ["bunch"]),
        ("Rainbow Chard", ["bunch"]), ("Organic Honeycrisp Apples", ["each", "3 lb bag"]),
        ("Organic Bananas", ["bunch"]), ("Organic Blueberries", ["6 oz", "pint"]),
        ("Organic Strawberries", ["1 lb"]), ("Raspberries", ["6 oz"]),
        ("Florida Mangoes", ["each", "2 ct"]), ("Papaya", ["each"]), ("Dragon Fruit", ["each"]),
        ("Passion Fruit", ["3 ct"]), ("Key Limes", ["1 lb bag"]), ("Organic Lemons", ["each", "2 lb bag"]),
        ("Organic Sweet Potatoes", ["1 lb"]), ("Organic Carrots", ["2 lb", "bunch"]),
        ("Broccolini", ["bunch"]), ("Organic Cauliflower", ["head"]), ("Zucchini", ["1 lb"]),
        ("Persian Cucumbers", ["1 lb"]), ("Organic Bell Peppers", ["each", "3 ct"]),
        ("Shallots", ["8 oz"]), ("Organic Garlic", ["3 ct"]), ("Ginger Root", ["4 oz"]),
        ("Turmeric Root", ["4 oz"]), ("Cilantro", ["bunch"]), ("Mint", ["bunch"]),
        ("Sunflower Microgreens", ["2 oz"]), ("Organic Arugula", ["5 oz"]), ("Spring Mix", ["5 oz"]),
        ("Shiitake Mushrooms", ["8 oz"]), ("Oyster Mushrooms", ["6 oz"]), ("Plantains", ["each", "3 ct"]),
        ("Young Coconut", ["each"]), ("Starfruit", ["each"]), ("Lychee", ["1 lb"]),
        ("Sweet Corn", ["2 ct"]), ("Radishes", ["bunch"]), ("Organic Beets", ["bunch"]),
        ("Asparagus", ["bunch"]), ("Haricots Verts", ["12 oz"]), ("Snap Peas", ["8 oz"]),
        ("Cherry Tomatoes", ["pint"]), ("Fennel", ["each"]), ("Organic Limes", ["each"]),
        ("Avocado, Florida", ["each"]), ("Guava", ["1 lb"]), ("Butter Lettuce", ["head"]),
        ("Cut Watermelon Cup", ["16 oz"]), ("Cut Mango Cup", ["12 oz"]), ("Cut Cantaloupe Cup", ["16 oz"]),
    ],
    "Meat & Seafood": [
        ("Grass-Fed Ribeye", ["12 oz", "16 oz"]), ("Grass-Fed Ground Beef", ["1 lb"]),
        ("Grass-Fed NY Strip", ["12 oz"]), ("Grass-Fed Filet Mignon", ["8 oz"]),
        ("Pasture-Raised Whole Chicken", ["4 lb"]), ("Pasture-Raised Chicken Thighs", ["1.5 lb"]),
        ("Pasture-Raised Chicken Breast", ["1 lb"]), ("Pasture-Raised Chicken Wings", ["2 lb"]),
        ("Heritage Pork Chops", ["1 lb"]), ("Uncured Bacon", ["12 oz"]), ("Lamb Loin Chops", ["12 oz"]),
        ("Ground Bison", ["1 lb"]), ("Wild Gulf Shrimp", ["1 lb"]), ("Wild Salmon Fillet", ["8 oz", "1 lb"]),
        ("Florida Snapper Fillet", ["1 lb"]), ("Mahi-Mahi Fillet", ["1 lb"]), ("Yellowfin Tuna Steak", ["8 oz"]),
        ("Dry-Packed Sea Scallops", ["8 oz"]), ("Chicken Sausage", ["12 oz"]), ("Grass-Fed Beef Hot Dogs", ["10 oz"]),
        ("Pasture-Raised Ground Turkey", ["1 lb"]), ("Grass-Fed Short Ribs", ["1.5 lb"]),
        ("Whole Branzino", ["each"]), ("Wild Cod Fillet", ["1 lb"]), ("Grass-Fed Skirt Steak", ["1 lb"]),
    ],
    "Dairy & Eggs": [
        ("Pasture-Raised Eggs", ["dozen", "6 ct"]), ("A2 Whole Milk", ["half gallon", "quart"]),
        ("Raw Milk Cheddar", ["8 oz"]), ("Grass-Fed Butter", ["8 oz"]), ("Cultured Butter", ["8 oz"]),
        ("Plain Greek Yogurt", ["32 oz", "5.3 oz"]), ("Coconut Yogurt", ["16 oz"]), ("Plain Kefir", ["32 oz"]),
        ("Cottage Cheese", ["16 oz"]), ("Chevre", ["4 oz"]), ("Burrata", ["8 oz"]),
        ("Parmigiano Reggiano", ["6 oz"]), ("Grass-Fed Ghee", ["8 oz", "16 oz"]), ("Heavy Cream", ["pint"]),
        ("Cultured Sour Cream", ["16 oz"]), ("Oat Milk", ["half gallon"]), ("Almond Milk", ["quart"]),
        ("Cashew Cheese", ["6 oz"]), ("Icelandic Skyr", ["5.3 oz"]), ("Duck Eggs", ["6 ct"]),
        ("Sheep Milk Feta", ["6 oz"]), ("Fresh Mozzarella", ["8 oz"]), ("Labneh", ["8 oz"]),
        ("Raw Cashew Cheesecake, Key Lime", ["4 oz"]), ("Raw Cashew Cheesecake, Strawberry", ["4 oz"]),
        ("Raw Vegan Guacamole", ["8 oz"]), ("A2 Chocolate Milk", ["quart"]),
    ],
    "Pantry": [
        ("Extra Virgin Olive Oil", ["500 ml", "1 L"]), ("Avocado Oil", ["500 ml"]), ("Virgin Coconut Oil", ["14 oz"]),
        ("Raw Wildflower Honey", ["12 oz", "16 oz"]), ("Maple Syrup", ["8 oz", "12 oz"]), ("Einkorn Pasta", ["12 oz"]),
        ("Chickpea Pasta", ["8 oz"]), ("Cassava Flour", ["16 oz"]), ("Almond Flour", ["16 oz"]),
        ("Sprouted Rolled Oats", ["24 oz"]), ("Grain-Free Granola", ["10 oz"]), ("Tricolor Quinoa", ["16 oz"]),
        ("Black Beans", ["15 oz"]), ("Chickpeas", ["15 oz"]), ("Chicken Bone Broth", ["24 oz"]),
        ("Marinara Sauce", ["24 oz"]), ("Tahini", ["16 oz"]), ("Almond Butter", ["12 oz"]),
        ("Cashew Butter", ["10 oz"]), ("Coconut Aminos", ["8 oz"]), ("Apple Cider Vinegar", ["16 oz"]),
        ("Flaky Sea Salt", ["8 oz"]), ("Datil Pepper Hot Sauce", ["5 oz"]), ("Mango Salsa", ["16 oz"]),
        ("Guava Jam", ["10 oz"]), ("Date Syrup", ["12 oz"]), ("Coconut Sugar", ["16 oz"]),
        ("Raw Cacao Powder", ["8 oz"]), ("Sardines in Olive Oil", ["4.4 oz"]), ("Wild Tuna in Olive Oil", ["5 oz"]),
        ("Sprouted Brown Rice", ["2 lb"]), ("French Lentils", ["16 oz"]), ("Everything Seasoning", ["2 oz"]),
        ("Ceylon Cinnamon", ["2 oz"]), ("Tomato Paste", ["7 oz"]), ("Coconut Milk", ["13.5 oz"]),
        ("Cassava Pancake Mix", ["16 oz"]), ("Sourdough Loaf", ["24 oz"]), ("Avocado Oil Mayo", ["12 oz"]),
        ("Unsweetened Ketchup", ["11 oz"]), ("Stone-Ground Mustard", ["8 oz"]), ("Olive Oil Dressing", ["8 oz"]),
        ("Basil Pesto", ["6 oz"]), ("Castelvetrano Olives", ["10 oz"]), ("Whole Peeled Tomatoes", ["28 oz"]),
        ("Pistachio Butter", ["16 oz"]), ("Raw Tahini", ["16 oz"]), ("Raw Bee Pollen", ["8 oz"]),
        ("Grain-Free O's Cereal, Honey", ["8 oz"]), ("Grain-Free O's Cereal, Cocoa", ["8 oz"]),
        ("Protein O's Cereal", ["8 oz"]), ("Manuka Honey", ["8 oz"]), ("Single-Origin Coffee Beans", ["12 oz"]),
    ],
    "Snacks": [
        ("Cassava Tortilla Chips", ["5 oz"]), ("Plantain Chips", ["5 oz"]), ("Coconut Oil Potato Chips", ["5 oz"]),
        ("Grain-Free Crackers", ["4.25 oz"]), ("Seed Crackers", ["4 oz"]), ("72% Dark Chocolate Bar", ["2.5 oz"]),
        ("Almond Butter Cups", ["4 ct"]), ("Grass-Fed Beef Sticks", ["1 oz", "4 ct"]), ("Date Protein Bar", ["2 oz", "4 ct"]),
        ("Tropical Trail Mix", ["8 oz"]), ("Sprouted Almonds", ["8 oz"]), ("Dried Mango", ["4 oz"]),
        ("Toasted Coconut Chips", ["3 oz"]), ("Coconut Oil Popcorn", ["4 oz"]), ("Grain-Free Cookies", ["6 oz"]),
        ("Date Bites", ["5 oz"]), ("Seaweed Snacks", ["6 pk"]), ("Kale Chips", ["2 oz"]),
        ("Pasture-Raised Pork Rinds", ["2.5 oz"]), ("Sourdough Pretzels", ["8 oz"]), ("Fruit Gummies", ["4 oz"]),
        ("Dark Chocolate Almonds", ["5 oz"]), ("Cacao Nibs", ["8 oz"]), ("Guava Pastelito Bites", ["6 oz"]),
        ("Chili Lime Cashews", ["6 oz"]), ("Sea Salt Macadamias", ["5 oz"]), ("Mango Chili Fruit Leather", ["1 oz"]),
        ("Coconut Macaroons", ["6 oz"]), ("Pumpkin Seed Clusters", ["5 oz"]), ("Sweet Potato Chips", ["5 oz"]),
        ("Stuffed Medjool Dates", ["4 ct"]), ("Olive & Pepperoncini Snack Cup", ["4 oz"]), ("Raw Protein Bar", ["2 oz"]),
        ("Brown Butter Cookie", ["each"]), ("Dark Chocolate Peanut Butter Cups", ["6 oz"]),
    ],
    "Beverages & Juices": [
        ("Cold-Pressed Green Juice", ["12 oz", "16 oz"]), ("Cold-Pressed Orange Juice", ["12 oz"]),
        ("Celery Juice", ["16 oz"]), ("Ginger Shot", ["2 oz"]), ("Turmeric Shot", ["2 oz"]),
        ("Coconut Water", ["12 oz", "1 L"]), ("Ginger Kombucha", ["16 oz"]), ("Guava Kombucha", ["16 oz"]),
        ("Sparkling Mineral Water", ["12 oz", "750 ml"]), ("Cold Brew Coffee", ["10 oz"]), ("Matcha Tonic", ["12 oz"]),
        ("Prebiotic Soda", ["12 oz"]), ("Yerba Mate", ["15.5 oz"]), ("Watermelon Juice", ["16 oz"]),
        ("Key Lime Lemonade", ["16 oz"]), ("Electrolyte Water", ["16 oz"]), ("Sea Moss Drink", ["12 oz"]),
        ("Beet Juice", ["12 oz"]), ("Pineapple Mint Juice", ["12 oz"]), ("Tart Cherry Juice", ["16 oz"]),
        ("Hibiscus Iced Tea", ["16 oz"]), ("Coconut Kefir", ["12 oz"]), ("Carrot Ginger Juice", ["12 oz"]),
        ("Alkaline Water", ["1 L"]), ("Pineapple Tepache", ["12 oz"]), ("Oat Milk Cold Brew Latte", ["11 oz"]),
        ("Sparkling Guava Water", ["12 oz"]), ("Oxygenated Spring Water", ["1 gal"]), ("Glass-Bottle Spring Water", ["1 L"]),
        ("Fresh-Squeezed Orange Juice", ["16 oz"]),
    ],
    "Supplements": [
        ("Magnesium Glycinate", ["120 ct"]), ("Vitamin D3 + K2", ["60 ct"]), ("Omega-3 Fish Oil", ["60 ct", "120 ct"]),
        ("Probiotic 50B", ["30 ct"]), ("Collagen Peptides", ["10 oz", "20 oz"]), ("Grass-Fed Whey Protein", ["2 lb"]),
        ("Plant Protein", ["1 lb"]), ("Creatine Monohydrate", ["300 g"]), ("Ashwagandha", ["60 ct"]),
        ("Lion's Mane", ["60 ct"]), ("Electrolyte Powder", ["30 ct"]), ("Sea Moss Gel", ["16 oz"]),
        ("Beef Organ Complex", ["180 ct"]), ("Buffered Vitamin C", ["90 ct"]), ("Zinc Picolinate", ["60 ct"]),
        ("Women's Multivitamin", ["60 ct"]), ("Men's Multivitamin", ["60 ct"]), ("Greens Powder", ["30 servings"]),
        ("Mushroom Coffee", ["30 servings"]), ("Elderberry Syrup", ["4 oz"]), ("Colostrum", ["120 ct"]),
        ("Methylated B-Complex", ["60 ct"]), ("Turmeric Curcumin", ["90 ct"]), ("Digestive Enzymes", ["90 ct"]),
        ("Magnesium Threonate", ["90 ct"]), ("Iron Bisglycinate", ["60 ct"]), ("Quercetin", ["60 ct"]),
        ("Methylated B12", ["60 ct"]), ("Reishi Extract", ["2 oz"]), ("L-Glutamine", ["300 g"]),
        ("Creatine Gummies", ["60 ct"]), ("Adaptogen Cacao Powder", ["6 oz"]), ("Herbal Tincture, Calm", ["1 oz"]),
        ("Beef Protein Isolate", ["1.5 lb"]), ("Hydration Sport Drink Mix", ["16 oz"]),
    ],
    "Beauty & Body": [
        ("Whipped Tallow Balm", ["2 oz"]), ("Mineral Sunscreen SPF 30", ["3 oz"]), ("Reef-Safe Sunscreen SPF 50", ["3 oz"]),
        ("Aluminum-Free Deodorant", ["2.5 oz"]), ("Castile Soap", ["16 oz"]), ("Dry Body Oil", ["4 oz"]),
        ("Vitamin C Face Serum", ["1 oz"]), ("Hyaluronic Serum", ["1 oz"]), ("Clay Mask", ["2 oz"]),
        ("Lip Balm Trio", ["3 pk"]), ("Clarifying Shampoo", ["12 oz"]), ("Hydrating Conditioner", ["12 oz"]),
        ("Body Wash", ["16 oz"]), ("Fluoride-Free Toothpaste", ["4 oz"]), ("Dry Shampoo", ["2 oz"]),
        ("Gentle Face Cleanser", ["4 oz"]), ("Daily Moisturizer", ["1.7 oz"]), ("Eye Cream", ["0.5 oz"]),
        ("Hand Cream", ["3 oz"]), ("Magnesium Body Spray", ["4 oz"]), ("Hair Oil", ["2 oz"]),
        ("Mineral Bath Soak", ["16 oz"]), ("Aloe After-Sun Gel", ["6 oz"]), ("Sea Salt Texture Spray", ["4 oz"]),
        ("Scalp Scrub", ["6 oz"]), ("Rosehip Face Oil", ["1 oz"]), ("Charcoal Soap Bar", ["5 oz"]),
        ("Tinted Mineral SPF", ["1.7 oz"]),
    ],
}

# Cafe & Hot Bar: brand "House Kitchen", named "Product: Variant"
CAFE_ITEMS = {
    "Smoothie": ["Adaptogen Cacao", "Detox Greens", "Mango Turmeric", "Espresso Maca", "Strawberry Collagen",
                 "Blueberry Almond", "Sunrise Citrus", "Coconut Vanilla", "Classic", "Seasonal"],
    "Acai Bowl": ["Classic", "Tropical", "Protein", "Pitaya", "Peanut Butter", "Seasonal"],
    "Hot Bar (per lb)": ["Grass-Fed Flank Steak", "Rigatoni Vodka", "Roasted Broccoli", "Crispy Chicken Tenders",
                         "Tallow Roasted Potatoes", "Sweet Potato Mash", "Wild Mushrooms", "Herb Grilled Chicken",
                         "Kale Salad", "Seasonal"],
    "Grain Bowl": ["Classic", "Mediterranean", "Seasonal", "Protein", "Vegan", "Spicy"],
    "Salad": ["Green", "Caesar", "Kale", "Seasonal", "Cobb", "Protein"],
    "Wrap": ["Chicken", "Veggie", "Turkey", "Breakfast", "Mediterranean"],
    "Bone Broth Bowl": ["Classic", "Ginger", "Turmeric"],
    "Soup and Side": ["Seasonal", "Chicken", "Lentil", "Black Bean", "Tomato"],
    "Toast Plate": ["Avocado", "Seasonal", "Almond Butter", "Smoked Salmon"],
    "Sandwich": ["Turkey Pesto", "Chicken Caprese", "Veggie", "Cuban", "Egg Salad"],
    "Matcha Bar": ["Ceremonial", "Cacao Adaptogen", "Coconut Cloud", "Iced Vanilla"],
    "Frozen Yogurt": ["Passion Fruit", "Coconut", "Cacao"],
    "Chia Pudding Parfait": ["Classic", "Mango", "Berry"],
    "Breakfast Burrito": ["Classic", "Veggie", "Chorizo"],
    "Poke Bowl": ["Classic", "Spicy", "Salmon", "Tofu"],
    "Protein Box": ["Classic", "Keto", "Vegan"],
    "Empanada Plate": ["Beef", "Chicken", "Spinach"],
    "Cuban Bowl": ["Classic", "Picadillo", "Vegan"],
    "Taco Plate": ["Fish", "Chicken", "Shrimp", "Vegan"],
}

# Ingredients that put a SKU on the clean-standard watchlist, by category.
FLAGGED_INGREDIENTS = {
    "Dairy & Eggs": ["guar gum", "gellan gum", "natural flavors", "carrageenan gum"],
    "Pantry": ["canola oil", "sunflower oil", "xanthan gum", "natural flavors"],
    "Snacks": ["sunflower oil", "canola oil", "natural flavors", "stevia"],
    "Beverages & Juices": ["stevia", "monk fruit", "natural flavors", "gellan gum"],
    "Supplements": ["stevia", "monk fruit", "natural flavors", "xanthan gum"],
    "Beauty & Body": ["artificial fragrance", "artificial colors"],
}

SHELF_LIFE_DAYS = {
    "Produce": (4, 10), "Meat & Seafood": (3, 7), "Dairy & Eggs": (10, 35), "Pantry": (180, 720),
    "Snacks": (90, 365), "Beverages & Juices": (3, 3), "Cafe & Hot Bar": (1, 1),
    "Supplements": (365, 730), "Beauty & Body": (365, 1095),
}

# (vendor_id, name, is_local, lead_time_days, min_order_usd, payment_terms, category focus)
KEY_VENDORS = [
    ("V000", "In-house kitchen", 1, 0, 0, "n/a", "Cafe & Hot Bar"),
    ("V001", "Biscayne Fresh Co.", 1, 1, 300, "Net 15", "Produce"),
    ("V002", "Palmetto Ranch Direct", 1, 2, 750, "Net 30", "Meat & Seafood"),
    ("V003", "Wildroot Wellness Supply", 0, 7, 1000, "Net 30", "Supplements"),
    ("V004", "Seagrape Juice Works", 1, 1, 250, "Net 15", "Beverages & Juices"),
    ("V005", "Northfield Pantry Distribution", 0, 5, 1500, "Net 45", "Pantry"),
]

VENDOR_WORDS_A = ["Atlantic", "Gulfstream", "Evergreen", "Heron", "Sunline", "Tropic", "Coral", "Harbor",
                  "Summit", "Riverbend", "Keystone", "Prairie", "Cedar", "Bayfront", "Lighthouse", "Osprey",
                  "Magnolia", "Blue Marlin", "Sandbar", "Cypress"]
VENDOR_WORDS_B = ["Organics", "Provisions", "Natural Foods", "Supply Co.", "Distributors", "Wholesale",
                  "Trading Co.", "Botanicals", "Brands", "Growers"]
