export const site = {
  name: "SpectralLAS",
  version: "0.1.0 · pre-alpha",
  repositoryUrl: (import.meta.env.VITE_REPOSITORY_URL ?? "").trim() || "https://github.com/Louis-Gm/spectral-las",
  pypiUrl: "https://pypi.org/project/spectral-las/",
  docsUrl: (import.meta.env.VITE_DOCS_URL ?? "").trim() || `${import.meta.env.BASE_URL}guide.html`,
  installCommand: "python -m pip install spectral-las",
};
