import styles from "./SummaryCards.module.css";

export default function SummaryCards({ results }) {
  const csBom    = results.cs_bom    || [];
  const bomExcel = results.bom_excel || [];
  const sapData  = results.sap_data  || {};

  // New shape: { entries: [{key, value}], design_text: "..." }
  const entries  = sapData.entries || [];

  // Pull key metadata for the badge strip from the entries list
  const findEntry = (key) =>
    entries.find((e) => e.key === key)?.value || null;

  const pumpName = findEntry("VT pump Common Name") || findEntry("Pump Common Name");
  const stages   = findEntry("No of Stages");

  const cards = [
    {
      title: "CS Drawing BOM",
      value: `${csBom.length} parts`,
      color: "var(--color-accent)",
    },
    {
      title: "Excel BOM",
      value: `${bomExcel.length} line items`,
      color: "var(--color-success)",
    },
    {
      title: "SAP Data",
      value: `${entries.length} fields`,
      color: "#8b5cf6",
    },
  ];

  return (
    <div className={styles.wrapper}>
      <div className={styles.grid}>
        {cards.map((c) => (
          <div key={c.title} className={styles.card}>
            <div className={styles.indicator} style={{ background: c.color }} />
            <div>
              <p className={styles.cardTitle}>{c.title}</p>
              <p className={styles.cardValue}>{c.value}</p>
            </div>
          </div>
        ))}
      </div>
      {(pumpName || stages) && (
        <div className={styles.meta}>
          {pumpName && <span className={styles.badge}>{pumpName}</span>}
          {stages   && <span className={styles.badge}>{stages} Stages</span>}
        </div>
      )}
    </div>
  );
}