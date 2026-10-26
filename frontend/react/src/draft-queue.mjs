// One queue per immutable relay turn. Local text is never replaced by poll data.
export class DraftQueue {
  constructor({turn, version=0, answer='', save, notify=()=>{}, delay=550, maxWait=1600}) {
    Object.assign(this,{turn,version,answer,save,notify,delay,maxWait});
    this.dirty=false; this.enabled=true; this.alive=true; this.inflight=null; this.conflict=false;
  }
  edit(answer) {
    this.answer=answer;this.dirty=true;this.notify('Unsaved changes');
    clearTimeout(this.debounce);
    this.debounce=setTimeout(()=>this.flush(),this.delay);
    if(!this.maximum)this.maximum=setTimeout(()=>this.flush(),this.maxWait);
  }
  async flush() {
    clearTimeout(this.debounce);clearTimeout(this.maximum);this.maximum=null;
    if(!this.alive||!this.enabled||this.conflict||this.inflight||!this.dirty)return;
    const answer=this.answer, version=++this.version;
    this.dirty=false;this.notify('Saving…');
    this.inflight=Promise.resolve().then(()=>this.save({turn_id:this.turn,version,answer}));
    try {await this.inflight;if(this.alive)this.notify(this.dirty?'Unsaved changes':'Saved to server');}
    catch(error) {
      this.dirty=true;
      if(error.status===409) {this.conflict=true;this.notify('Draft conflict. Restore the server draft before editing again.');}
      else if(error.status===401) {this.enabled=false;this.notify('Session ended. Changes were not saved.');}
      else {this.notify('Not saved · '+error.message);if(this.alive)this.retry=setTimeout(()=>{this.retry=null;this.flush()},2200);}
    } finally {this.inflight=null;if(this.alive&&this.enabled&&this.dirty&&!this.conflict&&!this.retry)this.debounce=setTimeout(()=>this.flush(),this.delay);}
  }
  setEnabled(value) {this.enabled=value;if(value&&this.dirty)this.flush();}
  stop() {this.alive=false;clearTimeout(this.debounce);clearTimeout(this.maximum);clearTimeout(this.retry);}
}
