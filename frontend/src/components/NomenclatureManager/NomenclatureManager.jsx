import { useState, useEffect, useCallback } from "react";
import {
  fetchNomenclature,
  addNomenclaturePart,
  deleteNomenclaturePart,
  addNomenclatureAlias,
  removeNomenclatureAlias,
  updateNomenclatureType,
} from "../../api/client";
import styles from "./NomenclatureManager.module.css";

const PART_TYPES = ["wetted_structural", "structural", "consumable", "accessory", ""];

export default function NomenclatureManager({ onClose }) {
  const [parts, setParts]           = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [search, setSearch]         = useState("");
  const [expanded, setExpanded]     = useState(null);
  const [newAlias, setNewAlias]     = useState({});
  const [showAddPart, setShowAddPart] = useState(false);
  const [newPart, setNewPart]       = useState({ canonical: "", type: "", aliases: "" });
  const [saving, setSaving]         = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchNomenclature();
      setParts(data.parts.sort((a, b) => a.canonical.localeCompare(b.canonical)));
    } catch (e) {
      setError(e.message || "Failed to load nomenclature");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = parts.filter(
    (p) =>
      p.canonical.toLowerCase().includes(search.toLowerCase()) ||
      p.aliases.some((a) => a.toLowerCase().includes(search.toLowerCase()))
  );

  async function handleAddAlias(canonical) {
    const alias = (newAlias[canonical] || "").trim();
    if (!alias) return;
    setSaving(true);
    try {
      await addNomenclatureAlias(canonical, alias);
      setNewAlias((prev) => ({ ...prev, [canonical]: "" }));
      await load();
    } catch (e) {
      setError(e.message || "Failed to add alias");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemoveAlias(canonical, alias) {
    if (!confirm(`Remove alias "${alias}" from "${canonical}"?`)) return;
    setSaving(true);
    try {
      await removeNomenclatureAlias(canonical, alias);
      await load();
    } catch (e) {
      setError(e.message || "Failed to remove alias");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeletePart(canonical) {
    if (!confirm(`Delete part "${canonical}" and all its aliases?`)) return;
    setSaving(true);
    try {
      await deleteNomenclaturePart(canonical);
      setExpanded(null);
      await load();
    } catch (e) {
      setError(e.message || "Failed to delete part");
    } finally {
      setSaving(false);
    }
  }

  async function handleTypeChange(canonical, type) {
    setSaving(true);
    try {
      await updateNomenclatureType(canonical, type);
      await load();
    } catch (e) {
      setError(e.message || "Failed to update type");
    } finally {
      setSaving(false);
    }
  }

  async function handleAddPart(e) {
    e.preventDefault();
    const canonical = newPart.canonical.trim();
    if (!canonical) return;
    const aliases = newPart.aliases
      .split("\n")
      .map((a) => a.trim())
      .filter(Boolean);
    setSaving(true);
    try {
      await addNomenclaturePart(canonical, newPart.type, aliases);
      setNewPart({ canonical: "", type: "", aliases: "" });
      setShowAddPart(false);
      await load();
    } catch (e) {
      setError(e.message || "Failed to add part");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.panel}>
        <div className={styles.header}>
          <h2 className={styles.title}>Nomenclature Manager</h2>
          <button className={styles.closeBtn} onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>

        <div className={styles.toolbar}>
          <input
            className={styles.search}
            placeholder="Search canonical names or aliases…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button
            className={styles.addPartBtn}
            onClick={() => setShowAddPart((v) => !v)}
          >
            {showAddPart ? "Cancel" : "+ Add Part"}
          </button>
        </div>

        {error && (
          <div className={styles.error}>
            {error}
            <button onClick={() => setError(null)}>&times;</button>
          </div>
        )}

        {showAddPart && (
          <form className={styles.addPartForm} onSubmit={handleAddPart}>
            <h3 className={styles.formTitle}>New Canonical Part</h3>
            <label className={styles.label}>
              Canonical name
              <input
                className={styles.input}
                required
                value={newPart.canonical}
                onChange={(e) => setNewPart((p) => ({ ...p, canonical: e.target.value }))}
                placeholder="e.g. Bearing Bush"
              />
            </label>
            <label className={styles.label}>
              Type
              <select
                className={styles.select}
                value={newPart.type}
                onChange={(e) => setNewPart((p) => ({ ...p, type: e.target.value }))}
              >
                {PART_TYPES.map((t) => (
                  <option key={t} value={t}>{t || "— unset —"}</option>
                ))}
              </select>
            </label>
            <label className={styles.label}>
              Aliases (one per line)
              <textarea
                className={styles.textarea}
                rows={4}
                value={newPart.aliases}
                onChange={(e) => setNewPart((p) => ({ ...p, aliases: e.target.value }))}
                placeholder="BRG BUSH&#10;BEARING BUSH&#10;INT. BEARING BUSH"
              />
            </label>
            <button className={styles.saveBtn} type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save Part"}
            </button>
          </form>
        )}

        {loading ? (
          <div className={styles.loading}>Loading…</div>
        ) : (
          <div className={styles.list}>
            <div className={styles.listMeta}>
              {filtered.length} of {parts.length} parts
            </div>
            {filtered.map((part) => {
              const isOpen = expanded === part.canonical;
              return (
                <div key={part.canonical} className={styles.partRow}>
                  <div
                    className={styles.partHeader}
                    onClick={() => setExpanded(isOpen ? null : part.canonical)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setExpanded(isOpen ? null : part.canonical)}
                  >
                    <span className={styles.caret}>{isOpen ? "▾" : "▸"}</span>
                    <span className={styles.canonicalName}>{part.canonical}</span>
                    {part.type && (
                      <span className={`${styles.typeBadge} ${styles[part.type]}`}>
                        {part.type}
                      </span>
                    )}
                    <span className={styles.aliasCount}>{part.aliases.length} aliases</span>
                  </div>

                  {isOpen && (
                    <div className={styles.partDetail}>
                      <div className={styles.typeRow}>
                        <span className={styles.detailLabel}>Type:</span>
                        <select
                          className={styles.typeSelect}
                          value={part.type}
                          onChange={(e) => handleTypeChange(part.canonical, e.target.value)}
                          disabled={saving}
                        >
                          {PART_TYPES.map((t) => (
                            <option key={t} value={t}>{t || "— unset —"}</option>
                          ))}
                        </select>
                      </div>

                      <div className={styles.aliasesLabel}>Aliases:</div>
                      <div className={styles.aliasesList}>
                        {part.aliases.length === 0 && (
                          <span className={styles.noAliases}>No aliases yet</span>
                        )}
                        {part.aliases.map((alias) => (
                          <div key={alias} className={styles.aliasItem}>
                            <span className={styles.aliasText}>{alias}</span>
                            <button
                              className={styles.removeAlias}
                              title="Remove alias"
                              disabled={saving}
                              onClick={() => handleRemoveAlias(part.canonical, alias)}
                            >
                              &times;
                            </button>
                          </div>
                        ))}
                      </div>

                      <div className={styles.addAliasRow}>
                        <input
                          className={styles.aliasInput}
                          placeholder="New alias…"
                          value={newAlias[part.canonical] || ""}
                          onChange={(e) =>
                            setNewAlias((prev) => ({
                              ...prev,
                              [part.canonical]: e.target.value,
                            }))
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              handleAddAlias(part.canonical);
                            }
                          }}
                          disabled={saving}
                        />
                        <button
                          className={styles.addAliasBtn}
                          disabled={saving || !newAlias[part.canonical]?.trim()}
                          onClick={() => handleAddAlias(part.canonical)}
                        >
                          Add
                        </button>
                      </div>

                      <button
                        className={styles.deletePartBtn}
                        disabled={saving}
                        onClick={() => handleDeletePart(part.canonical)}
                      >
                        Delete Part
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
