// Codificacion/decodificacion de frames protobuf de Pipecat, sin dependencias.
//
// Frame {
//   oneof frame {
//     TextFrame     text          = 1;
//     AudioRawFrame audio         = 2;
//     TranscriptionFrame transcription = 3;
//     MessageFrame  message       = 4;
//     InterruptionFrame interruption = 5;
//   }
// }
// AudioRawFrame { uint64 id=1; string name=2; bytes audio=3; uint32 sample_rate=4; uint32 num_channels=5; }
// TextFrame     { uint64 id=1; string name=2; string text=3; }
// TranscriptionFrame { uint64 id=1; string name=2; string text=3; string user_id=4; string timestamp=5; }
// MessageFrame  { string data=1; }

export const TASA = 16000;
export const CANALES = 1;

function varint(n) {
  const b = [];
  while (n > 127) { b.push((n & 0x7f) | 0x80); n >>>= 7; }
  b.push(n);
  return b;
}

function leerVarint(v, p) {
  let r = 0, s = 0, b;
  do { b = v[p.i++]; r |= (b & 0x7f) << s; s += 7; } while (b & 0x80);
  return r >>> 0;
}

/** PCM Int16 -> Frame.audio serializado */
export function codificarAudio(pcm) {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  const cuerpo = [
    0x1a, ...varint(bytes.length),            // campo 3: audio (bytes)
  ];
  const head = new Uint8Array(cuerpo);
  const cola = new Uint8Array([
    0x20, ...varint(TASA),                    // campo 4: sample_rate
    0x28, ...varint(CANALES),                 // campo 5: num_channels
  ]);
  const interno = new Uint8Array(head.length + bytes.length + cola.length);
  interno.set(head, 0);
  interno.set(bytes, head.length);
  interno.set(cola, head.length + bytes.length);

  const envoltura = new Uint8Array([0x12, ...varint(interno.length)]); // campo 2: audio
  const salida = new Uint8Array(envoltura.length + interno.length);
  salida.set(envoltura, 0);
  salida.set(interno, envoltura.length);
  return salida;
}

const ENC = new TextEncoder();

/** Objeto JSON -> Frame.message serializado (MessageFrame { string data = 1 }). */
export function codificarMensaje(obj) {
  const texto = ENC.encode(JSON.stringify(obj));
  const interno = new Uint8Array([0x0a, ...varint(texto.length), ...texto]);   // campo 1: data
  return new Uint8Array([0x22, ...varint(interno.length), ...interno]);         // Frame campo 4
}

/**
 * Mensaje RTVI "client-ready". OBLIGATORIO al abrir el socket.
 *
 * El servidor solo lanza el saludo inicial al recibirlo, y mientras el bot no
 * completa ese primer turno una estrategia de silencio IGNORA toda la voz del
 * usuario. Sin este mensaje: no hay saludo, la voz queda silenciada para
 * siempre y nada llega al modelo, sin ningún error visible.
 */
export function mensajeClienteListo() {
  return codificarMensaje({
    label: "rtvi-ai",
    type: "client-ready",
    id: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
    data: { version: "2.0.0", about: { library: "robot-web" } },
  });
}

const DEC = new TextDecoder();

function saltarCampo(v, p, tipo) {
  if (tipo === 0) leerVarint(v, p);
  else if (tipo === 2) { const n = leerVarint(v, p); p.i += n; }
  else if (tipo === 5) p.i += 4;
  else if (tipo === 1) p.i += 8;
}

/** Frame serializado -> {tipo, audio|texto|datos} */
export function decodificar(buf) {
  const v = new Uint8Array(buf);
  const p = { i: 0 };
  while (p.i < v.length) {
    const tag = leerVarint(v, p);
    const campo = tag >>> 3, tipo = tag & 7;
    if (tipo !== 2) { saltarCampo(v, p, tipo); continue; }
    const largo = leerVarint(v, p);
    const sub = v.subarray(p.i, p.i + largo);
    p.i += largo;

    if (campo === 2) return { tipo: "audio", audio: extraerBytes(sub, 3) };
    if (campo === 1) return { tipo: "texto", texto: extraerTexto(sub, 3) };
    if (campo === 3) return { tipo: "transcripcion", texto: extraerTexto(sub, 3) };
    if (campo === 4) return { tipo: "mensaje", datos: extraerTexto(sub, 1) };
    if (campo === 5) return { tipo: "interrupcion" };
  }
  return { tipo: "desconocido" };
}

function extraerBytes(sub, objetivo) {
  const p = { i: 0 };
  while (p.i < sub.length) {
    const tag = leerVarint(sub, p);
    const campo = tag >>> 3, tipo = tag & 7;
    if (tipo === 2) {
      const n = leerVarint(sub, p);
      if (campo === objetivo) return sub.subarray(p.i, p.i + n);
      p.i += n;
    } else saltarCampo(sub, p, tipo);
  }
  return new Uint8Array(0);
}

function extraerTexto(sub, objetivo) {
  const b = extraerBytes(sub, objetivo);
  return b.length ? DEC.decode(b) : "";
}
