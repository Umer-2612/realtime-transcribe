// AudioWorklet processor — must be served as a separate file over HTTP.
// Buffers mic input into 4096-sample chunks (256 ms @ 16 kHz),
// then posts each chunk to the main thread as a Float32Array.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(4096);
    this._pos = 0;
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this._buf[this._pos++] = ch[i];
      if (this._pos >= 4096) {
        this.port.postMessage(this._buf.slice());
        this._pos = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
