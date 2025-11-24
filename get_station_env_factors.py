import ee
import pandas as pd

# Initialize Earth Engine
ee.Initialize(project='co2-digital-twin')

# Load stations CSV
stations = pd.read_csv('station_loc.csv')

# Convert stations to FeatureCollection
features = []
for idx, row in stations.iterrows():
    point = ee.Geometry.Point([row['Lon'], row['Lat']])
    feature = ee.Feature(point, {
        'StationId': row['StationId'],
        'StationName': row['StationName'],
        'City': row['City'],
        'State': row['State']
    })
    features.append(feature)

fc = ee.FeatureCollection(features)

# Take first MODIS image
modis_image = ee.ImageCollection('MODIS/061/MCD43A3').first()

# Select bands you want
bands = ['Albedo_BSA_Band1', 'Albedo_BSA_Band2']  # Add more bands as needed
image = modis_image.select(bands)

# Reduce regions (compute mean for each station)
result_fc = image.reduceRegions(
    collection=fc,
    reducer=ee.Reducer.mean(),
    scale=500
)

# Get results as a list of dicts
def feature_to_dict(feature):
    return feature.getInfo()['properties']

results = [feature_to_dict(f) for f in result_fc.getInfo()['features']]

# Save results locally
df = pd.DataFrame(results)
df.to_csv('station_env_factors.csv', index=False)
print("Saved to station_env_factors.csv")
