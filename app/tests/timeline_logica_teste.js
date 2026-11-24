// Testes da lógica da linha do tempo (rodados pelo test_linha_do_tempo.py com o gjs). Imprime "OK" ou lança erro.
const GLib = imports.gi.GLib;
const [, bytes] = GLib.file_get_contents(ARGV[0]);
const fonte = new TextDecoder().decode(bytes);
const L = new Function(fonte + `; return {tlNovo, tlDuracao, tlPecaEm, tlDividir, tlAlternar, tlAparar, tlProximoMantido,
  tlTrechos, tlDeTrechos, tlFormatar, tlPassoRegua, tlHistNovo, tlHistAdicionar, tlHistDesfazer, tlHistRefazer,
  tlHistPodeDesfazer, tlHistPodeRefazer, tlInicio, tlFim};`)();

function igual(a, b, msg) {
  const x = JSON.stringify(a), y = JSON.stringify(b);
  if (x !== y) throw new Error(`${msg}: obtido ${x}, esperado ${y}`);
}

// vídeo sem cortes -> trechos null
let p = L.tlNovo(60);
igual(L.tlTrechos(p), null, "sem cortes");
igual(L.tlDuracao(p), 60, "duração inteira");

// dividir no meio e excluir o segundo pedaço
const d = L.tlDividir(p, 20);
igual(d.map((x) => [x.a, x.b, x.keep]), [[0, 20, true], [20, 60, true]], "dividir");
igual(p.length, 1, "não altera o original");
igual(L.tlDividir(p, 0.1), null, "perto da borda não divide");
igual(L.tlDividir(p, 59.9), null, "perto da borda (fim) não divide");
const e = L.tlAlternar(d, 1);
igual(L.tlTrechos(e), [[0, 20]], "excluir o último pedaço");
igual(L.tlDuracao(e), 20, "duração depois de excluir");
igual(L.tlTrechos(L.tlAlternar(e, 1)), null, "restaurar volta ao vídeo inteiro");

// excluir um pedaço do meio
let m = L.tlDividir(L.tlDividir(p, 10), 30);
m = L.tlAlternar(m, 1);
igual(L.tlTrechos(m), [[0, 10], [30, 60]], "trecho do meio excluído");
igual(L.tlProximoMantido(m, 15), 30, "pula o excluído");
igual(L.tlProximoMantido(m, 5), 5, "no mantido não pula");
igual(L.tlProximoMantido(L.tlAlternar(L.tlDividir(p, 40), 1), 50), null, "excluído até o fim -> null");

// não deixa o vídeo ficar com menos de 1 segundo
igual(L.tlDividir(p, 0.15), null, "peça mínima de 0.2");
const quase = L.tlDividir(p, 30);
const q1 = L.tlAlternar(quase, 1);
igual(L.tlAlternar(q1, 0), null, "tudo excluído não pode");

// aparar as pontas (a partir do estado antes do arraste)
let a = L.tlAparar(p, "ini", 5);
igual(L.tlTrechos(a), [[5, 60]], "aparar início");
igual(a.length, 2, "ponta aparada vira uma peça só");
a = L.tlAparar(a, "fim", 50);
igual(L.tlTrechos(a), [[5, 50]], "aparar fim");
const volta = L.tlAparar(a, "ini", 2);                          // arrastar a alça de volta
igual(L.tlTrechos(volta), [[2, 50]], "puxar a alça de volta devolve parte");
igual(L.tlTrechos(L.tlAparar(volta, "ini", 0)), [[0, 50]], "alça no zero");
igual(L.tlInicio(a), 5, "início"); igual(L.tlFim(a), 50, "fim");
igual(L.tlAparar(p, "ini", 59.9).filter((x) => x.keep).map((x) => [x.a, x.b])[0][1], 60, "o início para 1s antes do fim");
igual(L.tlDuracao(L.tlAparar(p, "ini", 59.9)) >= 1, true, "mínimo de 1s ao aparar");
igual(L.tlDuracao(L.tlAparar(p, "fim", 0.1)) >= 1, true, "mínimo de 1s ao aparar o fim");

// aparar não mexe nos cortes do meio
const mm = L.tlAparar(m, "ini", 5);
igual(L.tlTrechos(mm), [[5, 10], [30, 60]], "aparar com corte no meio");

// reconstruir das opções salvas
const r = L.tlDeTrechos([[5, 10], [30, 60]], 60);
igual(r.map((x) => [x.a, x.b, x.keep]), [[0, 5, false], [5, 10, true], [10, 30, false], [30, 60, true]], "de trechos");
igual(L.tlTrechos(r), [[5, 10], [30, 60]], "ida e volta");
igual(L.tlTrechos(L.tlDeTrechos(null, 60)), null, "sem trechos salvos");
igual(L.tlTrechos(L.tlDeTrechos([[-5, 90]], 60)), null, "trechos fora do vídeo são limitados");

// histórico
let h = L.tlHistNovo(p);
igual(L.tlHistPodeDesfazer(h), false, "nada para desfazer");
L.tlHistAdicionar(h, d);
L.tlHistAdicionar(h, e);
igual(L.tlHistPodeDesfazer(h), true, "pode desfazer");
igual(L.tlTrechos(L.tlHistDesfazer(h)), null, "desfazer 1 (volta ao dividido, sem cortes)");
igual(L.tlHistDesfazer(h).length, 1, "desfazer 2 (inteiro)");
igual(L.tlHistPodeDesfazer(h), false, "fim do histórico");
igual(L.tlHistPodeRefazer(h), true, "pode refazer");
L.tlHistRefazer(h);
igual(L.tlTrechos(L.tlHistRefazer(h)), [[0, 20]], "refazer até o corte");
L.tlHistDesfazer(h); L.tlHistAdicionar(h, m);
igual(L.tlHistPodeRefazer(h), false, "nova ação descarta o refazer");

// formatação e régua
igual(L.tlFormatar(0), "00:00", "0s");
igual(L.tlFormatar(65.7), "01:05", "65s");
igual(L.tlFormatar(65.7, true), "01:05.7", "decimos");
igual(L.tlFormatar(3725), "1:02:05", "horas");
igual(L.tlPassoRegua(60 / 800, 70) >= 5, true, "passo da régua");
print("OK");
