# Evaluation: hazard-blind baseline vs hazard-aware routing

500 random trips 3-12 km apart (same trips in every scenario), lambda = 0.5. Times are car, normal traffic. Exposure = metres of road inside hazard zones weighted by severity.

## 1. Hazard exposure

| Scenario | Trips | Baseline route blocked | Baseline route hit by hazard | Hazard-aware unreachable | Mean exposure, baseline | Mean exposure, aware | Reduction |
|---|---|---|---|---|---|---|---|
| flood | 500 | 8.6% | 17.4% | 0.4% | 307.02 | 72.36 | 76.4% |
| wildfire | 500 | 0.2% | 6.4% | 0.2% | 1426.7 | 807.27 | 43.4% |
| landslide | 500 | 0.2% | 5.4% | 0.0% | 619.44 | 235.57 | 62.0% |
| earthquake | 500 | 8.4% | 8.6% | 0.2% | 33.53 | 33.53 | 0.0% |

Exposure columns use trips whose baseline route is hit but not blocked (a blocked route cannot be driven).

## 2. Travel-time trade-off (trips whose baseline route was hit)

| Scenario | Affected trips | Median extra minutes | Mean extra minutes | Median extra % (graph time) |
|---|---|---|---|---|
| flood | 87 | 3.96 | 5.97 | 8.9 |
| wildfire | 32 | 3.83 | 5.45 | 10.0 |
| landslide | 27 | 3.61 | 5.15 | 8.8 |
| earthquake | 43 | 2.35 | 2.87 | 5.7 |

## 3. Rerouting speed (normal conditions to scenario, median per trip)

| Scenario | D* Lite nodes | Fresh ALT nodes | D* Lite ms | Fresh ALT ms | Same cost |
|---|---|---|---|---|---|
| flood | 0.0 | 2036.5 | 3.71 | 13.47 | 500/500 |
| wildfire | 0.0 | 1760.5 | 9.8 | 11.73 | 500/500 |
| landslide | 0.0 | 1784.0 | 2.58 | 11.96 | 500/500 |
| earthquake | 0.0 | 1855.0 | 0.84 | 22.76 | 500/500 |

## 4. Facility access (share of road network within 10 real car minutes of a facility)

| Scenario | Hospitals normal | Hospitals in scenario | Hospitals cut off | Shelters normal | Shelters in scenario | Shelters cut off |
|---|---|---|---|---|---|---|
| flood | 92.0% | 91.52% | 2 | 63.01% | 61.82% | 2 |
| wildfire | 92.0% | 90.75% | 1 | 63.01% | 62.14% | 0 |
| landslide | 92.0% | 91.5% | 0 | 63.01% | 62.88% | 0 |
| earthquake | 92.0% | 91.95% | 1 | 63.01% | 62.35% | 1 |

## 5. Time versus exposure as lambda grows (trips whose baseline route was hit or blocked)

| Scenario | lambda | Affected trips | Mean exposure | Mean extra minutes | Unreachable |
|---|---|---|---|---|---|
| flood | 0 | 87 | 173.84 | 5.14 | 2 |
| flood | 0.5 | 87 | 58.94 | 5.97 | 2 |
| flood | 2 | 87 | 25.48 | 7.22 | 2 |
| flood | 5 | 87 | 24.6 | 7.32 | 2 |
| wildfire | 0 | 32 | 989.7 | 4.81 | 1 |
| wildfire | 0.5 | 32 | 807.27 | 5.45 | 1 |
| wildfire | 2 | 32 | 758.87 | 6.91 | 1 |
| wildfire | 5 | 32 | 756.56 | 7.2 | 1 |
| landslide | 0 | 27 | 424.54 | 3.95 | 0 |
| landslide | 0.5 | 27 | 252.91 | 5.15 | 0 |
| landslide | 2 | 27 | 181.16 | 7.04 | 0 |
| landslide | 5 | 27 | 147.81 | 10.28 | 0 |
| earthquake | 0 | 43 | 1.6 | 2.87 | 1 |
| earthquake | 0.5 | 43 | 1.6 | 2.87 | 1 |
| earthquake | 2 | 43 | 0.0 | 2.92 | 1 |
| earthquake | 5 | 43 | 0.0 | 2.92 | 1 |
