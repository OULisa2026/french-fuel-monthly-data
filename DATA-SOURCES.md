# Public data and calculation

Producer: DGCCRF / Ministères économiques et financiers, France. Source: [official fuel-price annual archives](https://www.prix-carburants.gouv.fr/rubrique/opendata/), downloaded from `https://donnees.roulez-eco.fr/opendata/annee/YYYY`.

Fuel observations and the derived monthly summary are reused under [Licence Ouverte 2.0](https://www.data.gouv.fr/pages/legal/licences/etalab-2.0). Attribute the producer, source and last observation date. This independent data project is not endorsed by the French government.

`data/history.json` records the observation cutoff, generation time, source URLs, SHA-256 archive hashes, observation count and station counts. The monthly workflow includes the last 12 complete calendar months and supports 6/12-month consumers. First average each station/fuel/month's distinct timestamped reported prices; then average station means equally within each department. Missing station-months are omitted. No interpolation or carry-forward is used. This is a monthly reported-price statistic, not a sales-weighted or time-weighted average, and not the history of a user's selected cheapest stations.

This repository contains no user location, account credential, company-name database, AIUI application source, OpenAI API call or AI task. Public government summaries and the small reproducible updater are the publication scope. Keep temporary annual ZIP/XML archives outside the published files.
