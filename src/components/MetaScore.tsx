/** Ocena Metacritic. Sama liczba, bez słowa — działa w każdym języku, a kolor mówi
 *  to samo bez czytania. Wspólna dla naszego ekranu i sekcji na ekranie gry Steama. */
export function MetaScore({ score }: { score?: number | null }) {
  if (typeof score !== "number") return null;
  return (
    <div
      style={{
        background: score >= 75 ? "#66cc33" : score >= 50 ? "#ffcc33" : "#ff6b6b",
        color: "#1a1a1a",
        fontWeight: "bold",
        fontSize: "0.8em",
        padding: "1px 6px",
        borderRadius: "3px",
        flex: "0 0 auto",
      }}
    >
      {score}
    </div>
  );
}
