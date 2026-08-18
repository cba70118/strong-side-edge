# The Best Public NFL Modelers — Who They Are and How They Think

*A field guide to the most respected people building NFL projection and betting models, with publicly available resources on their processes and principles. Compiled August 2026.*

---

## 1. Sean Koerner — The Action Network

**Who he is.** Director (now Senior Director) of Predictive Analytics at The Action Network. Before that he spent roughly a decade at STATS Inc. (now Stats Perform), where he ran their industry-standard fantasy projections. He is a four-time winner of FantasyPros' most-accurate fantasy football ranker award — arguably the best public track record in projections. He also has a background in weather forecasting, which shows up in how he talks about probability: everything is a distribution, not a point estimate.

**How he works (from public material).** Koerner's public writing reveals a few consistent principles. He projects in *tiers* rather than strict ranks, because the error bars on adjacent players are wider than the gaps between them — grouping players by expected value acknowledges model uncertainty instead of hiding it. He updates continuously as news arrives rather than publishing static rankings, and his draft advice is framed around *opportunity cost* — compare the drop-off at each position to the next round rather than taking the "best" player in a vacuum. His weekly work leans on probabilistic outputs (e.g., "odds of a top-12 finish") rather than single-number projections.

**Where to learn from him:**
- [His Action Network author page](https://www.actionnetwork.com/article/author/sean-koerner) — weekly tiers, projections methodology notes in-article
- [Koerner's Fantasy Tiers and draft strategy (FantasyLabs)](https://www.fantasylabs.com/articles/koerners-fantasy-tiers-updated-expert-fantasy-football-rankings-draft-strategy/) — the clearest public articulation of his tier/drop-off process
- [Projecting every player's odds of a top fantasy finish](https://www.actionnetwork.com/nfl/week-3-fantasy-football-projections-odds-sean-koerner) — an example of his distribution-first output style
- [Fantasy Flex podcast](https://podcasts.apple.com/us/podcast/fantasy-flex/id1579363969) — his weekly show, where he explains reasoning behind projection changes
- [His LinkedIn](https://www.linkedin.com/in/seankoerner) confirms the STATS/Action Network arc; [Stats Perform's projections page](https://www.statsperform.com/resource/stats-fantasy-projections-an-industry-leader/) describes the system he built there

---

## 2. Rufus Peabody (with Cade Massey) — Massey-Peabody Analytics / Unabated

**Who he is.** Professional sports bettor and co-founder of [Massey-Peabody Analytics](https://massey-peabody.com/) with Wharton professor Cade Massey. One of the few genuinely sharp bettors who talks openly about process. Co-founded Unabated, a bettor-education platform. Regular [MIT Sloan Sports Analytics Conference speaker](https://www.sloansportsconference.com/people/rufus-peabody).

**How the model works (from public material).** The Massey-Peabody power ratings are built from play-by-play data distilled into four performance factors — rushing, passing, scoring, and play success — adjusted for opponent, home field, and game situation. Two stated principles distinguish it. First, "clean the variables": get a less noisy version of each input than everyone else by carefully contextualizing situation and opponent. Second, and Massey calls this their biggest edge: **weight variables by out-of-sample predictive power, not in-sample correlation.** They deliberately ignore personnel, coaching, and motivation narratives. Massey is also publicly humble about ceilings — their long-run ~56% against the spread is presented as evidence of how much irreducible luck is in football, not as a small number ([Wharton alumni profile](https://alumni.wharton.upenn.edu/all-stories/data-and-analytics/prof-cade-massey-explains-the-analytics-behind-his-nfl-rankings-how-data-science-led-this-undergrad-to-business-analytics/)).

**Where to learn from him:**
- [Bet the Process podcast](https://podcasts.apple.com/us/podcast/bet-the-process/id1291010585) — co-hosted with Jeff Ma (of MIT blackjack team fame); the single best ongoing public resource on professional betting process
- [Rufus on the Unabated Podcast: NFL, modeling, and the future of betting](https://open.spotify.com/episode/1gL6hedtYeBxUujaEHsEQB)
- [BettorIQ "A Sports Betting Education" session on handicapping styles and modelers with Peabody](https://bettoriq.com/education/a-sports-betting-education-session-2-handicapping-styles-and-modelers-with-rufus-peabody/)
- [Rufus interviewed on Kevin Cole's Unexpected Points](https://www.unexpectedpoints.com/p/rufus-peabody-of-massey-peabody-analytics-381)
- [Unabated's NFL ratings tools](https://unabated.com/nfl/ratings) and their [education library](https://unabated.com/education/the-art-of-sports-betting), plus the [user's guide to betting NFL with Unabated](https://unabated.com/articles/users-guide-betting-on-the-nfl-using-unabated)

---

## 3. Ben Baldwin — nflfastR / rbsdm.com / The Athletic

**Who he is.** An economist by training who became the most influential open-source figure in NFL analytics. With Sebastian Carl he built [nflfastR](https://nflfastr.com/), the free play-by-play data package with EPA, win probability, completion probability, and xPass models baked in, and [rbsdm.com](https://rbsdm.com), the free dashboard that made EPA charts ubiquitous. His stated philosophy: "it's kind of lame to say people should use EPA but not provide a way for people to get EPA easily" — and, notably, "EPA is not a magic bullet for solving football" ([theScore profile](https://www.thescore.com/nfl/news/2193857/how-one-nfl-advanced-statistic-is-going-mainstream)).

**Why he matters for learning process.** Baldwin is unique because his models are *fully open source* — you can read the actual code and the papers behind it, not just descriptions:
- [How nflfastR's EP, WP, CP, xYAC and xPass models work](https://opensourcefootball.com/posts/2020-09-28-nflfastr-ep-wp-and-cp-models/) — full methodology write-up with model features and calibration
- [A beginner's guide to nflfastR](https://nflfastr.com/articles/beginners_guide.html) and the [get started guide](https://nflfastr.com/articles/nflfastR.html)
- [Open Source Football](https://opensourcefootball.com/) — the peer-reviewed-ish blog he co-founded, e.g. [exploring rolling averages of EPA](https://opensourcefootball.com/posts/2020-12-29-exploring-rolling-averages-of-epa/) (how many games of EPA data are predictive — a core stability question)

---

## 4. Aaron Schatz — DVOA (FTN, formerly Football Outsiders)

**Who he is.** The godfather of public NFL team-efficiency modeling. Created DVOA (Defense-adjusted Value Over Average) at Football Outsiders in 2003; now Chief Analytics Officer at FTN, which publishes the annual FTN Football Almanac.

**How DVOA works (from public material).** DVOA evaluates every play against a league-average baseline *for that situation* — a 5-yard gain on 3rd-and-4 counts for more than 5 yards on 1st-and-10 — using "success points" tied to progress toward first downs and scoring, then adjusts everything for opponent quality. The core principle: context beats raw totals, and situation-specific baselines are how you encode context. The methodology has been continuously revised in public ([DVOA v8.0 announcement](https://www.ftnfantasy.com/articles/FTN/103397/introducing-dvoa-v80)).

**Where to learn from him:**
- [DVOA explained (FTN)](https://ftnfantasy.com/learn-more-about-dvoa) and the [DVOA stat explainer](https://ftnfantasy.com/nfl/dvoa-explainer)
- [The FTN Football Almanac](https://ftnfantasy.com/almanac) — annual, with methodology essays
- [His FTN author page](https://ftnfantasy.com/contributor/aaronschatz) and [interview on DVOA changes and team projections](https://www.acmepackingcompany.com/2024/8/14/24219745/packers-2024-projections-advanced-stats-aaron-schatz-ftn-interview-route-dvoa-dyar-jeff-hafley)

---

## 5. Kevin Cole — Unexpected Points

**Who he is.** Former head of quantitative research at PFF and a RotoGrinders alum; now writes [Unexpected Points](https://www.unexpectedpoints.com/), the best substack for someone who wants to see NFL betting-model construction shown step by step, with code and honest post-mortems.

**Why he matters for learning process.** Cole publishes the kind of material most modelers keep private: how to structure a betting model, backtests, feature choices, and interviews with sharps. His [2018 RotoGrinders betting model series](https://rotogrinders.com/articles/kevin-cole-s-2018-nfl-betting-model-week-1-2647571) walked through a full model build in public. His [podcast/YouTube](https://www.youtube.com/@unexpectedpoints) features long-form process conversations with people like [Peabody](https://www.unexpectedpoints.com/p/rufus-peabody-of-massey-peabody-analytics-381) and [Ben Baldwin](https://www.unexpectedpoints.com/p/ranking-nfl-front-offices-w-ben-baldwin).

---

## 6. Eric Eager — SumerSports (formerly PFF)

**Who he is.** A math professor turned VP of research at PFF, now at SumerSports (an NFL analytics firm founded by ex-Milwaukee Bucks owner Marc Lasry's circle). Co-hosted the PFF Forecast; brings an academic modeling background ([SIAM profile](https://www.siam.org/programs-initiatives/professional-development/career-resources/careers-in-applied-mathematics/careers-brochure/eric-eager/), [his research site](https://sites.google.com/site/ericeageranalytics/home/sports-analytics)).

**Where to learn from him:**
- [SumerSports' The Zone blog](https://sumersports.com/the-zone/welcome-to-sumersports-com/) — public model write-ups, e.g. their [in-game coaching model introduction](https://sumersports.com/the-zone/sumersports-in-game-coaching-model-an-introduction/)
- [SumerSports Show podcast](https://podcasts.apple.com/us/podcast/sumersports-show/id1648601469) and [PFF Forecast crossover episodes](https://open.spotify.com/episode/1lnJjzkfoaLonYixkka03t)

---

## 7. Institutional models worth studying

**ESPN FPI.** ESPN has published unusually detailed methodology notes: [how FPI was developed](https://www.espn.com/nfl/story/_/id/13539941/how-espn-nfl-football-power-index-was-developed-implemented), [the FPI introduction](https://www.espn.com/nfl/story/_/id/13539793/espn-nfl-football-power-index-debuts), and [a guide to NFL FPI](https://www.espn.com/blog/statsinfo/post/_/id/123048/a-guide-to-nfl-fpi). FPI is an EPA-based team-strength model with priors (preseason expectations from Vegas totals, returning starters, etc.) that shrink as the season provides data — a textbook Bayesian structure stated plainly.

**FiveThirtyEight Elo (historical, but fully open).** The complete [NFL Elo model code and data are on GitHub](https://github.com/fivethirtyeight/nfl-elo-game) with the [archive of Elo articles](https://fivethirtyeight.com/tag/nfl-elo-ratings/). Simple, transparent, and a great baseline to learn from — including its QB adjustment layer.

**Josh Hermsmeyer (air yards / receiver modeling).** Invented the air-yards framework and RACR while at FiveThirtyEight; built [airyards.com](https://airyards.com/). His FiveThirtyEight piece on [building a better matchup metric](https://fivethirtyeight.com/features/its-hard-to-measure-nfl-matchups-so-we-built-a-better-metric/) is a model-construction walkthrough, and his [4for4 archive](https://www.4for4.com/users/josh-hermsmeyer/author-page) continues the usage-first receiver work.

---

## 8. Books and ongoing education

- **[The Logic of Sports Betting](https://www.amazon.com/Logic-Sports-Betting-Ed-Miller/dp/1096805723)** — Ed Miller & Matthew Davidow. Davidow is a professional modeler (Deck Prism Sports), and the book's core insight is about *market structure*: how lines are made, why synthetic hold matters, and why beating the close is the real test. The closest thing to a canonical text.
- **[Wharton Moneyball](https://knowledge.wharton.upenn.edu/shows/moneyball/)** — Cade Massey's radio show/podcast with fellow Wharton professors. Episodes like [Football Analytics, Probabilities, Priors, and Fourth-Down Decisions](https://shows.acast.com/knowledge-at-wharton/episodes/football-analytics-at-work-probabilities-priors-and-fourth-d) and [Inside NFL Chaos: Power Rankings, Prediction, and Team Strength](https://knowledge.wharton.upenn.edu/podcast/moneyball/inside-nfl-chaos-power-rankings-prediction-and-team-strength/) are free graduate seminars in prediction under uncertainty.
- **[Bet the Process](https://podcasts.apple.com/us/podcast/bet-the-process/id1291010585)** — Peabody & Jeff Ma, as above.
- **[The Power Rank's "Craft of Sports Betting Professionals"](https://thepowerrank.com/the-craft-of-sports-betting-professionals-2/)** — Ed Feng's synthesis of interviews with pros, including [Captain Jack Andrews](https://thepowerrank.libsyn.com/captain-jack-andrews-on-sports-betting-in-2022).

---

## The principles that keep recurring

Across all of these modelers, the same handful of ideas show up independently — which is itself evidence they're the real fundamentals.

**The market is the benchmark, not the enemy.** Peabody, Miller/Davidow, and Koerner all treat the closing line as the strongest available prediction. The goal is not to out-model the market wholesale but to find specific spots it hasn't absorbed — and the honest scorecard is closing line value, not win-loss record.

**Predictive beats descriptive.** Massey-Peabody's stated core edge is weighting inputs by *out-of-sample* predictive power. Schatz's DVOA and Baldwin's EPA work exist because yards and points describe the past, while situation-adjusted efficiency predicts the future. Ask of every stat: does it stabilize, and does it forecast?

**Adjust for context or don't bother.** Opponent adjustment, situation baselines (down-and-distance), garbage time, and home field appear in every serious model — DVOA, Massey-Peabody, FPI, nflfastR's EP models all encode this.

**Regress and use priors.** FPI starts from preseason priors that decay as data arrives. Koerner's tiers exist because small differences are noise. Everyone regresses small-sample stats (turnovers, red-zone rates) hard toward the mean.

**Output distributions, not points.** Koerner's odds-of-finish framing, Massey's "irreducible uncertainty" talk, and prop modelers' skew-aware projections all reflect the same discipline: a projection without an error bar is an opinion.

**Ignore narratives.** Massey-Peabody explicitly excludes motivation, revenge, and coaching-drama inputs. If it can't be measured and shown to predict, it's not in the model.

**Show your work and update in public.** The most respected figures (Baldwin, Schatz, Cole) publish methodology, revise it openly, and admit what their models can't do — "EPA is not a magic bullet."

---

*Prepared for C — August 16, 2026. All links verified accessible at compile time.*
