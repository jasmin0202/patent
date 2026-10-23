# mcp/

외부 MCP 서버를 두는 자리. 이 폴더의 서버 소스는 저장소에 포함하지 않는다(`.gitignore`). 각자 받아서 쓴다.

| 폴더 | 출처 | 용도 | 설치 |
|---|---|---|---|
| `kipris-mcp/` | [SilverQ/kipris-mcp](https://github.com/SilverQ/kipris-mcp) (MIT) | KIPRIS Plus 특허·실용신안 검색을 `mcp__kipris__*` 도구로 제공 | 루트 `README.md`의 "KIPRIS MCP 설치" |

`.mcp.json`이 `mcp/kipris-mcp/src/index.js`를 가리키므로 폴더 이름을 바꾸면 거기도 같이 바꾼다. API 키는 환경변수 `KIPRIS_API_KEY`로만 전달하고 파일에 적지 않는다.
