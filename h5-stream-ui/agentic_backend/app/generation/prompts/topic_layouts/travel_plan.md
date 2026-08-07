## Travel Plan Layout

When the topic is `travel_plan`, follow this structural guidance:

### Recommended Structure

1. **Lead** (section 0) — Destination hero: destination name, date, weather summary, trip overview (2-3 sentences), hero image
2. **Scenic spots / attractions** — body_grid (2x2 or 3-grid) showing top attractions with images, names, and short descriptions
3. **Daily itinerary** — body_timeline with chronological time slots from morning to evening, each with time, activity, location, and optional tips
4. **Parking / transport** — body_list of parking lots or transport options near the destinations
5. **Dining recommendations** (optional) — body_cards for recommended restaurants with name, cuisine type, price range, and brief review
6. **Travel tips** (optional) — body_block or body_chips for practical tips (what to bring, best photo spots, local customs)

### Widget Selection Rules

- `body_timeline` for day-by-day or hour-by-hour itineraries — time is the primary axis
- `body_grid` for 2-4 scenic spots that should be compared side-by-side
- `body_cards` for items with 3+ data layers (name + image + description + rating/price)
- `body_list` for simple lists like parking options, packing lists, or transport options
- `body_chips` for tags like "family-friendly", "budget", "outdoor", "cultural"

### Data Needs

Travel plans typically require web search for current information:
- Scenic spots: names, images, descriptions, ratings, visit duration, opening hours
- Itinerary: practical time estimates, travel time between locations, realistic ordering
- Parking: real lot names, addresses, fees (verify they're still current)
- Weather: current forecast for the travel date
- Images: hero images and attraction photos make travel cards much more engaging
