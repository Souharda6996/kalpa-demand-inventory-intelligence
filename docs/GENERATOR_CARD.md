# Generator card - Bengaluru FreshMart (synthetic)

Synthetic data. Results on it show the model recovers injected effects; accuracy results on it are reported as synthetic-data results.

## Config

| parameter | value |
|---|---|
| seed | 42 |
| start | 2023-01-01 |
| end | 2025-12-31 |
| annual_trend | 0.08 |
| yearly_amp | 0.1 |
| festival_ramp_days | 7 |
| promo_prob_per_week | 0.04 |
| promo_discount_range | (0.1, 0.3) |
| price_elasticity | -1.4 |
| temp_beverage_slope | 0.03 |
| nb_dispersion | 8.0 |
| intermittent_share | 0.2 |
| intermittent_scale | 0.12 |
| store_scale_sd | 0.25 |
| sku_scale_sd | 0.45 |

## Categories

| category | #SKUs | base units/day/store | price INR | weekend lift | festival amplitudes |
|---|---|---|---|---|---|
| Sweets | 30 | 6.0 | 480 | 0.25 | {'Diwali': 2.5, 'Holi': 0.8, 'Eid al-Fitr': 1.0, 'Ganesh Chaturthi': 1.0, 'Onam': 0.8, 'Durga Puja': 1.0} |
| Dry Fruits | 30 | 4.0 | 900 | 0.15 | {'Diwali': 2.0, 'Eid al-Fitr': 1.2, 'Christmas': 0.6} |
| Colours | 30 | 1.5 | 90 | 0.1 | {'Holi': 6.0} |
| Beverages | 30 | 14.0 | 60 | 0.3 | {'Holi': 0.4, 'Independence Day': 0.2} |
| Dairy | 30 | 18.0 | 55 | 0.05 | {'Pongal/Sankranti': 0.5, 'Onam': 0.4} |
| Staples | 30 | 12.0 | 120 | 0.05 | {'Diwali': 0.3, 'Eid al-Fitr': 0.4, 'Pongal/Sankranti': 0.4} |
| Snacks | 30 | 10.0 | 40 | 0.35 | {'Diwali': 0.6, 'Holi': 0.5, 'Christmas': 0.3} |
| Fresh Produce | 30 | 16.0 | 45 | 0.1 | {'Pongal/Sankranti': 0.6, 'Onam': 0.6} |
| Personal Care | 30 | 5.0 | 180 | 0.1 | {'Diwali': 0.4} |
| Bakery | 30 | 8.0 | 70 | 0.2 | {'Christmas': 1.5} |

## Network

Stores: Koramangala, Indiranagar, Whitefield, Jayanagar, HSR Layout, Malleshwaram; DC: Peenya DC; suppliers: 8.

Festival dates: `festivals.csv` (`source` column marks library-derived vs hand-curated dates).
Weather in this generator is synthetic (sinusoid + noise); real Open-Meteo history is fetched by `weather.py`.
