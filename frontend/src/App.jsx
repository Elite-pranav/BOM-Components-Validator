import { useState } from "react";
import Header from "./components/Header/Header";
import Footer from "./components/Footer/Footer";
import UploadSection from "./components/UploadSection/UploadSection";
import ProgressIndicator from "./components/ProgressIndicator/ProgressIndicator";
import ResultsSection from "./components/ResultsSection/ResultsSection";
import ValidationSection from "./components/ValidationSection/ValidationSection";
import { uploadDocuments, triggerExtraction, runComparison } from "./api/client";
import styles from "./App.module.css";

export default function App() {
  // Steps: upload → extracting → results → comparing → validation
  const [step, setStep] = useState("upload");
  const [files, setFiles] = useState({ cs: null, bom: null, sap: null });
  const [identifier, setIdentifier] = useState(null);
  const [results, setResults] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [error, setError] = useState(null);

  function handleFileChange(type, file) {
    setFiles((prev) => ({ ...prev, [type]: file }));
    setError(null);
  }

  async function handleExtract() {
    setError(null);
    setStep("extracting");

    try {
      const uploadRes = await uploadDocuments(files.cs, files.bom, files.sap);
      const id = uploadRes.identifier;
      setIdentifier(id);

      const extractRes = await triggerExtraction(id);
      setResults(extractRes.results);
      setStep("results");
    } catch (err) {
      setError(err.message || "Something went wrong");
      setStep("upload");
    }
  }

  async function handleCompare() {
    setError(null);
    setStep("comparing");

    try {
      const res = await runComparison(identifier);
      setComparison(res.comparison);
      setStep("validation");
    } catch (err) {
      setError(err.message || "Comparison failed");
      setStep("results");
    }
  }

  function handleReset() {
    setStep("upload");
    setFiles({ cs: null, bom: null, sap: null });
    setIdentifier(null);
    setResults(null);
    setComparison(null);
    setError(null);
  }

  return (
    <div className={styles.app}>
      <Header />
      <main className={styles.main}>
        {error && (
          <div className={styles.error}>
            <span>{error}</span>
            <button onClick={() => setError(null)}>&times;</button>
          </div>
        )}

        {step === "upload" && (
          <UploadSection
            files={files}
            onFileChange={handleFileChange}
            onExtract={handleExtract}
            loading={false}
          />
        )}

        {step === "extracting" && <ProgressIndicator />}

        {step === "results" && results && (
          <ResultsSection
            results={results}
            identifier={identifier}
            onReset={handleReset}
            onCompare={handleCompare}
            comparing={false}
          />
        )}

        {step === "comparing" && <ProgressIndicator />}

        {step === "validation" && comparison && (
          <ValidationSection
            comparison={comparison}
            identifier={identifier}
            onBack={() => setStep("results")}
          />
        )}
      </main>
      <Footer />
    </div>
  );
}
