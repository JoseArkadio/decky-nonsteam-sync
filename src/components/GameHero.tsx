/** Nagłówek prawej sekcji: pozioma okładka gry jako baner, tytuł na niej.
 *
 *  Trzy rzeczy, które wyglądają na ozdobę, a nie są:
 *
 *  1. **Gradient pod tytułem jest obowiązkowy.** Okładka bywa jasna (Steam trzyma je
 *     takimi, jakie wgra SteamGridDB), a biały tytuł na jasnym zdjęciu znika. Gradient
 *     do przezroczystości u góry zostawia zdjęcie widoczne i daje tytułowi tło.
 *  2. **Brak okładki NIE usuwa nagłówka.** Zostaje ten sam prostokąt z gradientem
 *     i dużym inicjałem tytułu w tle — dwie gry obok siebie mają wyglądać tak samo,
 *     a dziura w układzie wyglądałaby jak awaria (a nie jak „nie pobrano grafik").
 *  3. **`objectFit: cover`**, bo kandydaci mają różne proporcje i rozciągnięta okładka
 *     rzuca się w oczy bardziej niż przycięta.
 *
 *  Kandydata NIE szukamy tutaj — dostajemy go z zewnątrz (`useCover` w `GameDetails`).
 *  Powód: tę samą grafikę pokazuje też przyciemnione tło całej sekcji, a dwa osobne
 *  wywołania haka to dwa niezależne liczniki prób — baner mógłby pokazywać drugiego
 *  kandydata, a tło pierwszego (czyli 404 i puste tło przy widocznym banerze). */
/** Kandydat na grafikę razem z obsługą 404 — kształt zwracany przez `useCover`. */
interface Cover {
  src: string | null;
  onError: () => void;
}

export function GameHero({ art, title }: { art: Cover; title: string }) {
  const initial = title.trim().charAt(0).toUpperCase();

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "132px",
        flexShrink: 0,
        borderRadius: "10px",
        overflow: "hidden",
        background: "linear-gradient(135deg, #2a3a4c 0%, #121820 100%)",
      }}
    >
      {art.src ? (
        <img
          src={art.src}
          alt=""
          onError={art.onError}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <div
          aria-hidden
          style={{
            position: "absolute",
            left: "16px",
            top: "50%",
            transform: "translateY(-50%)",
            fontSize: "86px",
            fontWeight: "bold",
            lineHeight: 1,
            color: "rgba(255, 255, 255, 0.09)",
          }}
        >
          {initial}
        </div>
      )}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(to top, rgba(9,13,18,0.94) 0%, rgba(9,13,18,0.55) 40%, rgba(9,13,18,0) 80%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "14px",
          right: "14px",
          bottom: "10px",
          fontSize: "1.5em",
          fontWeight: "bold",
          lineHeight: 1.15,
          overflowWrap: "anywhere",
          textShadow: "0 2px 6px rgba(0, 0, 0, 0.7)",
        }}
      >
        {title}
      </div>
    </div>
  );
}
