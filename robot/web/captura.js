// AudioWorklet: reenvia bloques de microfono al hilo principal como Float32.
class CapturaMicro extends AudioWorkletProcessor {
  process(entradas) {
    const canal = entradas[0]?.[0];
    if (canal && canal.length) {
      this.port.postMessage(new Float32Array(canal));
    }
    return true;
  }
}
registerProcessor("captura-micro", CapturaMicro);
