import styles from "./SummaryCards.module.css";

export default function SummaryCards({ results }) {
  const csBom = results.cs_bom || [];
  const bomExcel = results.bom_excel || [];
  const sapData = results.sap_data || {};
  const sapParts = sapData.parts ? Object.keys(sapData.parts).length : 0;
  const sapMeta = sapData.metadata ? Object.keys(sapData.metadata).length : 0;

  // Try to pull key metadata for display
  const meta = sapData.metadata || {};
  const pumpName = meta["VT pump Common Name"] || meta["Pump Common Name"] || null;
  const stages = meta["No of Stages"] || null;

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
      value: `${sapParts} parts, ${sapMeta} fields`,
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
          {stages && <span className={styles.badge}>{stages} Stages</span>}
        </div>
      )}
    </div>
  );
}
