# Jarvis Verse level system

Status: **built 2026-10-03.** Backend `jarvis/progression/`, REST
`/api/progression`, frontend `components/society/progression/`. The rulebook
in `jarvis/progression/rules.py` is the source of truth; this page is its
readable version.

## 1. Who levels up

| Subject | Id | Earns from |
|---|---|---|
| The person | `person` | Talking to Jarvis, giving the team work, growing the team, small actions in the Verse |
| Every Jarvis agent | `agent:<agent_id>` | Finished work: results, quests, answers to teammates |
| The person's pet | `pet:<pet_id>` | Jarvis's own work (the pet IS Jarvis in the Verse) and walks with the person |

The lead agent (`jarvis`) never has an agent subject: it is drawn as the pet,
so its work pays the pet. Each pet keeps its own level; the pet chosen in
My Pets at the moment of the award earns it.

Coding-floor panes are not subjects: they are short-lived terminal sessions,
not members of the team.

## 2. XP rules

Every award comes from one rule: a fixed amount, optionally a cooldown, a
daily cap (local calendar day) or "once per reference". The server applies
every limit against its own ledger, so a replayed event or a client that
reports twice never pays twice. Nothing calls a model; XP costs no tokens and
works with any single key.

**Server rules** (read off bus events):

| Source | Pays | XP | Limit | Event |
|---|---|---|---|---|
| `chat_turn` | person | 5 | 100/day | `JarvisChatTurnFinished` (done) |
| `voice_turn` | person | 5 | 100/day | `VoiceTurnCompleted` |
| `jarvis_answered` | pet | 4 | 120/day | either of the two above |
| `quest_posted` | person | 15 | 75/day | `SocietyQuestChanged` (new) |
| `quest_completed` | person | 20 | — | `SocietyQuestChanged` → done |
| `quest_done` | agent (taker) | 60 | — | `SocietyQuestChanged` → done |
| `task_done` | agent | 40 | — | `SocietyResultPosted` (done) |
| `task_blocked` | agent | 8 | 40/day | `SocietyResultPosted` (blocked) |
| `answered_teammate` | agent | 5 | 50/day | `SocietyMessageSent` ANSWER |
| `delegated` | pet | 10 | 100/day | `SocietyMessageSent` ASSIGN from the lead |
| `mission_completed` | person | 30 | 150/day | `MissionCompleted` (approved) |
| `agent_hired` | person | 50 | once per agent | `POST /api/society/agents` created a row |

**World rules** (reported by the Verse via `POST /api/progression/actions`):

| Source | Pays | XP | Limit |
|---|---|---|---|
| `daily_visit` | person | 25 | once per day (server date) |
| `floor_discovered` | person | 20 | once per floor (`agents`, `coding`, `arcade`) |
| `dog_petted` | person | 3 | every 60 s, 30/day |
| `dog_treat` | person | 10 | every 5 min, 30/day |
| `arcade_round` | person | 8 | every 45 s, 80/day (a scored round) |
| `arcade_record` | person | 15 | every 45 s, 60/day (a new best) |
| `team_meeting` | person | 10 | every 10 min, 40/day |
| `walk_together` | pet | 3 | every 20 s, 60/day (each 50 m walked with the pet) |

## 3. Level curve

Level *L* → *L + 1* costs `40 + 25·(L−1)^1.1` XP, rounded to 5; the cap is
level 50. Level 2 arrives after a handful of actions, level 10 after about a
week of ordinary use (~200 XP a day), level 50 after several months. The
server sends the whole curve (`level_xp`), so the client never re-derives it.

## 4. Titles and rewards

Titles change at bands: the person goes Newcomer → Apprentice (5) → Operator
(10) → Specialist (15) → Strategist (20) → Architect (25) → Commander (30) →
Visionary (40) → Legend (50); agents Rookie → … → Grandmaster; pets
Hatchling → … → Mythic.

Rewards are cosmetic and fill four slots. Each reward names the level at which
each subject kind unlocks it (or never):

| Slot | Rewards (person level) |
|---|---|
| Frame (level chip and HUD ring) | bronze 3, silver 10, gold 20, diamond 50 |
| Trail (left while moving) | footprints 2, sparkle 5, comet 12, neon 20, rainbow 30, star dust 45 |
| Aura (on the floor) | glow 7, runes 15, storm 35, legend 50 |
| Gadget (on or around the figure) | drone 10, halo 18, crown 25, light wings 40 |

The person chooses per slot for themself and their pet (automatic = the
latest unlock, none, or any unlocked piece; stored per browser profile).
Agents always wear their best unlocks.

## 5. The level-up moment

- **In the world:** a column of light from the floor, two shockwave rings,
  a fountain of sparks that rains back down and a rising "LEVEL n" tag, in
  gold (person), teal (pet) or green (agent). The person's figure hops and
  waves.
- **On screen:** person and pet get a banner — light rays and a flash, the
  word stamps in, the old number rolls out and the new one in, then the new
  title and each unlocked reward slide up one after another. Agents get a
  quiet toast in the corner. Level-ups that happened while the map was closed
  are told once per subject ("While you were away").
- **Sound:** a synthesised fanfare (Web Audio, no files), a short chime for an
  agent, a soft tick for "+XP"; switchable in the progress panel.
- **Every gain:** a floating "+n XP" over whoever earned it; the HUD bar lights
  the gain first and fills a beat later.

Reduced motion keeps the information (banner, tag, toast) and drops the motion.

## 6. Surfaces

- Level card under the floor title (click or `L` opens the progress panel).
- Level chip on every name plate; the lead's chip shows the pet's level.
- Progress panel: You / Pet / Agents tabs — level, wear choices, reward track,
  the exact rules; Agents is a ranking.

## 7. Contracts

- `ProgressionAwarded` (core bus → WebSocket) carries `subject_id`,
  `subject_kind`, `xp_source`, `xp`, `total_xp`, `level`, `previous_level`,
  `title`, `unlocked`.
- `SocietyResultPosted` is new: `WorldFeed` forwards a board RESULT (id,
  agent, status; never its text).
- `levelCatalog.ts` mirrors the ids (kinds, slots, rewards, titles, sources);
  `tests/unit/progression/test_frontend_parity.py` pins it and the three
  locale files to the rulebook.
- The ledger is `<data_dir>/progression.db`, opened on the first award, never
  on the boot path. Bus handlers only queue their writes, so a publisher (the
  voice pipeline) never waits on the disk.
