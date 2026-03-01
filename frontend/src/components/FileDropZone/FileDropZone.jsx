import { useRef, useState } from "react";
import { FiUpload, FiFile, FiX } from "react-icons/fi";
import styles from "./FileDropZone.module.css";

export default function FileDropZone({ label, accept, file, onFileSelect }) {
  const inputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) onFileSelect(dropped);
  }

  function handleChange(e) {
    const selected = e.target.files[0];
    if (selected) onFileSelect(selected);
  }

  function handleRemove(e) {
    e.stopPropagation();
    onFileSelect(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div
      className={`${styles.zone} ${dragOver ? styles.dragOver : ""} ${file ? styles.hasFile : ""}`}
      onClick={() => !file && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        className={styles.input}
      />
      {file ? (
        <div className={styles.fileInfo}>
          <FiFile className={styles.fileIcon} />
          <span className={styles.fileName}>{file.name}</span>
          <button className={styles.removeBtn} onClick={handleRemove} title="Remove file">
            <FiX />
          </button>
        </div>
      ) : (
        <div className={styles.placeholder}>
          <FiUpload className={styles.uploadIcon} />
          <span className={styles.label}>{label}</span>
          <span className={styles.hint}>Click or drag file here</span>
        </div>
      )}
    </div>
  );
}
