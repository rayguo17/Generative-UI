# Research Store

Offline data store for the Researcher agent. Instead of performing live internet searches, the Researcher queries this local file-based store for pre-cached research data.

## How It Works

1. **Pre-population** (done offline with cloud LLM / manual search):
   - Simulate the Plan Agent with sample queries
   - Perform real internet searches for each section's `data_needed`
   - Store results as structured markdown files organized by topic

2. **Runtime** (local LLM pipeline):
   - The Researcher reads `index.json` to find matching topic data
   - It loads relevant markdown files and extracts data for each section
   - No internet required — everything comes from local files

## Directory Structure

```
research_store/
├── README.md                    # This file
├── index.json                   # Master index: topic → query → files
└── travel/
    └── hangzhou/
        ├── overview.md          # Destination overview, weather, hero info
        ├── scenic_spots.md      # Top attractions with images, descriptions, ratings
        ├── itinerary.md         # Day-by-day itinerary with times, locations
        ├── parking_transport.md # Parking lots, transport options, fees
        ├── dining.md            # Restaurant recommendations by price tier
        └── travel_tips.md       # Practical tips, best seasons, dos and don'ts
```

## Adding New Research Data

1. Look at what topics/queries are planned in `index.json`
2. Run the plan agent and identify each section's `data_needed` fields
3. Search the web for each data category
4. Create markdown files under `research_store/<topic>/<subtopic>/`
5. Update `index.json` with the new query entry and file list

## File Format

Each data file is a standard markdown file. The Researcher uses keyword + heading matching to find relevant data for each section. Structure data with clear headings (##), tables, and lists for best retrieval accuracy.

## Integration

The `researcher.py` module reads from this directory when a topic/query match is found in `index.json`. The `gather_section_data()` function maps each section's `data_needed` to the appropriate research store files and extracts relevant content.
