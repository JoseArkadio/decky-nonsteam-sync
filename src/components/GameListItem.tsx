import { GameRecord } from "../backend";
import { useLandscape } from "../artwork";
import { gameStatus } from "../status";

/** Element spisu po lewej: pozioma okładka, tytuł, jedna linia statusu.
 *  Bez przycisków — wiersz jest wyłącznie nawigacją (na padzie A nie może
 *  robić dwóch różnych rzeczy zależnie od tego, gdzie stoi zaznaczenie).
 *
 *  Kolumna spisu ma na Decku ~310 px (ZMIERZONE na zrzucie ekranu), więc okładka
 *  jest mała, a tytuł ZAWIJA się do dwóch linii. Wcześniej był `nowrap` z „…" i
 *  nazwy wychodziły jako „007 First Lig" — obcięty tytuł jest bezużyteczny, bo to
 *  tytuł jest w tym projekcie tożsamością gry. */
export function GameListItem({ game }: { game: GameRecord }) {
  // Okładka: lista kandydatów i reguła 404 siedzą w `useLandscape` (artwork.ts).
  // Po wyczerpaniu kandydatów rysujemy szare pole — bez tego lista wyglądała jak
  // „gry bez grafik".
  const art = useLandscape(game.appid);
  const status = gameStatus(game);

  return (
    <div data-sdsync-item style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0, padding: "4px 0" }}>
      {art.src ? (
        <img
          src={art.src}
          alt=""
          onError={art.onError}
          style={{ width: "80px", height: "37px", objectFit: "cover", borderRadius: "3px", flexShrink: 0 }}
        />
      ) : (
        <div
          style={{
            width: "80px",
            height: "37px",
            borderRadius: "3px",
            flexShrink: 0,
            background: "rgba(255,255,255,0.1)",
          }}
        />
      )}
      <div style={{ minWidth: 0, flexGrow: 1 }}>
        <div style={{ whiteSpace: "normal", overflowWrap: "anywhere", lineHeight: 1.2 }}>
          {game.title}
        </div>
        <div
          style={{
            fontSize: "0.85em",
            // ZMIERZONE: bez tego Steam dziedziczy tu 22 px interlinii przy czcionce
            // 13,6 px, więc blok tekstu jest wyższy niż okładka obok i wiersz wygląda
            // na niewyrównany w pionie.
            lineHeight: 1.3,
            whiteSpace: "normal",
            overflowWrap: "anywhere",
            color: status.alarm ? "#ffb347" : undefined,
            opacity: status.alarm ? 1 : 0.7,
          }}
        >
          {status.text}
        </div>
      </div>
    </div>
  );
}
