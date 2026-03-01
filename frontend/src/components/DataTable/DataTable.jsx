import styles from "./DataTable.module.css";

export default function DataTable({ columns, rows }) {
  if (!rows || rows.length === 0) {
    return <p className={styles.empty}>No data available</p>;
  }

  return (
    <div className={styles.wrapper}>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} className={styles.th}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? styles.even : ""}>
              {columns.map((col) => (
                <td key={col.key} className={styles.td}>
                  {formatCell(row[col.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
