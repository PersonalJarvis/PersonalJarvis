/**
 * The front page's header row — intentionally empty.
 *
 * It used to show who is talking (name, voice state) and what answers
 * (engine + model pills) with a bottom border. Per user request the whole
 * row is gone: no border line, no name/state, no engine/model pills. The
 * component stays as a null stub so HomeView needs no structural change
 * and the area behind it stays the plain black page background.
 */
export function HomeHeader() {
  return null;
}
