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

## 4. Ranks and rewards

Every subject climbs one military rank ladder; the rank is its title. A
promotion comes every two or three levels (`RANKS` in rules.py):

| Tier | Ranks (first level) | Insignia |
|---|---|---|
| Enlisted | Private (1, no insignia), Private Second Class (2), Private First Class (4), Specialist (6) | Gold chevrons on navy, worn on the upper sleeves |
| Non-commissioned officers | Corporal (8), Sergeant (10), Staff Sergeant (12), Sergeant First Class (14), Master Sergeant (16), First Sergeant (18), Sergeant Major (20), Command Sergeant Major (22), Sergeant Major of the Army (24) | Chevrons, rockers and their device (diamond, star, wreath, eagle), on the sleeves |
| Officers | Second Lieutenant (26), First Lieutenant (28), Captain (30), Major (32), Lieutenant Colonel (34), Colonel (36) | Gold and silver bars, oak leaves, the eagle, on navy shoulder boards |
| General officers | Brigadier General (38), Major General (41), Lieutenant General (44), General (47), General of the Army (50) | One to five silver stars on black boards edged in gold |

The insignia are vector art (`progression/insignia/rankArt.ts`): one set of
polygons drives the SVG icons, the Level Wall's canvas and the extruded 3D
pieces on figures and in the hall's cases.

Rewards are real uniform pieces in three slots, each unlocking on a
promotion. The person wears them; agents keep the outfit their owner dressed
them in and earn decorations only; the pet wears its rank on its name plate.

| Slot | Pieces (person level · agent level) |
|---|---|
| Uniform | service shirt and tie 4, field jacket 10, green service uniform 20, blue dress uniform 26, blue mess dress 41 |
| Headwear | patrol cap 6, garrison cap 14, black beret 24, service cap 32 |
| Decoration | service ribbons 8 · 3, full ribbon rack 18 · 16, gold aiguillette 36 · 32, full-size medals 47 · 44 |

Trims follow the rank: officers' dress uniforms carry gold cuff braid,
non-commissioned officers and up a trouser stripe; the patrol cap carries
the sewn rank, the garrison cap and beret an officer's pin (a unit crest for
enlisted ranks), and the service cap's visor one row of gold oak leaves for
field-grade officers and two for generals.

The person chooses per slot (automatic = the finest unlock, own clothes, or
any unlocked piece; stored per browser profile). Agents always wear their
best unlocks.

### Agent looks

Agents also unlock the wearable looks of their profile symbol
(`LOOK_UNLOCKS` in `rules.py`, ids from `companion/accessories.json`):

| Agent level | Looks |
|---|---|
| 1 | sunglasses, cap, headphones, coder hoodie |
| 2 | lab coat |
| 3 | cigar |
| 4 | suit and tie |
| 6 | top hat |
| 8 | tuxedo |
| 10 | crown |

The snapshot carries them as `looks` and a level-up's `ProgressionAwarded`
names them in `unlocked_looks`. The agent's level-up toast shows the agent
already wearing its new look, the Level Hall's Team page lists every look
with its level and each agent's next one, and the Appearance picker marks a
locked look with the level it opens at. The lock is a reward, never a
penalty: a look an agent already wears stays wearable, and an unreachable
level system locks nothing.

## 5. The level-up moment

- **In the world:** a single gold ring runs out across the floor and a soft
  light fades under the figure, while a plate rises over its head with the
  new insignia — "Promoted · Sergeant" on a promotion, "Level 7" between
  two. The person's figure hops and waves.
- **On screen:** person and pet get the promotion card — midnight blue with a
  fine gold rule. On a promotion the old insignia stands, steps aside and
  dims while the new one is laid on piece by piece and catches the light;
  then the rank name, its grade and the rolling level number, the next
  promotion between two, and each unlocked uniform piece. Agents get a quiet
  toast in the corner. Level-ups that happened while the map was closed are
  told once per subject ("While you were away").
- **Sound:** a synthesised fanfare (Web Audio, no files), a short chime for an
  agent, a soft tick for "+XP"; switchable in the Level Hall screen.
- **Every gain:** a floating "+n XP" over whoever earned it; the HUD bar lights
  the gain first and fills a beat later.

Reduced motion keeps the information (card, plate, toast) and drops the motion.

## 6. Surfaces

- Level card under the floor title: the rank insignia in a ring that fills
  with XP, the rank and the bar (click or `L` opens the Level Hall screen).
- A rank chip (insignia and level) on every name plate; the lead's chip shows
  the pet's.
- On the figures: enlisted insignia on both upper sleeves, officer and
  general insignia on shoulder boards, the decoration over the left breast
  pocket (the aiguillette from the right shoulder), the cap on the head.
- **The Level Hall** on the agents floor (office-map.md §5f): the rank wall,
  the studio dais, a display case per uniform piece along the runner and the
  service guide. Stepping on the dais opens the screen's Studio page, the
  guide its Overview, a case its piece on the promotion road.
- **The Level Hall screen**, for the person or their pet (switch in the head):
  - *Overview:* rank, level, XP bar with what is missing, the next promotion,
    the next three uniform pieces (each opens in the Studio to try on) and the
    quickest repeatable ways to earn.
  - *Ranks:* the whole ladder as one chart, grouped by tier; ranks held in
    full colour, the current one framed, the ones ahead as silhouettes with
    the XP still missing.
  - *Studio:* the dressing room. A live 3D preview of the person's figure on
    the dais in the loadout and the insignia of their rank; per slot
    "automatic", "own clothes" and every piece as a card with the rank it
    comes with. An unlocked piece is put on with one click; a locked piece is
    tried on in the preview, marked "Try-on" with the level and XP it still
    needs. The pet's studio shows its rank.
  - *Promotions:* the road from the first promotion to the cap with the
    subject's position on it; the chosen step in the preview at that rank,
    with its insignia, grade, tier, piece, missing XP and "Put it on" once
    open.
  - *How to level:* three steps (earn, level up, get promoted), the cost of
    the next levels from the server's curve, and every rule for the person,
    the pet and agents, grouped.
  - *Team:* every agent's level and rank, best first.

## 7. Contracts

- `ProgressionAwarded` (core bus → WebSocket) carries `subject_id`,
  `subject_kind`, `xp_source`, `xp`, `total_xp`, `level`, `previous_level`,
  `title`, `unlocked`.
- `SocietyResultPosted` is new: `WorldFeed` forwards a board RESULT (id,
  agent, status; never its text).
- `levelCatalog.ts` mirrors the ids (kinds, slots, rewards, ranks, sources) and
  each rank's grade and tier;
  `tests/unit/progression/test_frontend_parity.py` pins it and the three
  locale files to the rulebook.
- The ledger is `<data_dir>/progression.db`, opened on the first award, never
  on the boot path. Bus handlers only queue their writes, so a publisher (the
  voice pipeline) never waits on the disk.
