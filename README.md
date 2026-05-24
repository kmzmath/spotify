# Math Spotify Dashboard — versão web

App Streamlit adaptado para rodar online com upload de arquivos pelo navegador.

## Arquivos esperados

Você pode enviar:

- `YourLibrary.json` — opcional, usado para identificar músicas curtidas.
- Um ou vários `Streaming_History_Audio_*.json`.
- Ou um `.zip` contendo esses JSONs.

## Rodar localmente

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

## Deploy no Streamlit Community Cloud

1. Suba estes arquivos para um repositório GitHub.
2. No Streamlit Community Cloud, crie um app apontando para `streamlit_app.py`.
3. Mantenha `requirements.txt` no mesmo diretório ou na raiz do repositório.
4. A pasta `.streamlit/config.toml` aumenta o limite de upload por arquivo para 1024 MB.

## Privacidade

Este app não grava os JSONs em disco. Os arquivos são lidos em memória durante a sessão do Streamlit.
