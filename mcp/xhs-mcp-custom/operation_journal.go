package main

// Indexed receipts live on disk. Only unresolved writes stay in runtime memory.
// bbolt is embedded, transactional and works with CGO disabled; no daemon or
// second queue is introduced. Every open is bounded and closed after use.
import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	bolt "go.etcd.io/bbolt"
)

var receiptBucket = []byte("receipts")
var fingerprintBucket = []byte("fingerprints")
var pendingBucket = []byte("unresolved")
var countBucket = []byte("counts")
var bindingBucket = []byte("binding")

func (s *stateStore) journalPath() string {
	return filepath.Join(filepath.Dir(s.path), "operation-journal.db")
}

func (s *stateStore) journal(readonly bool, fn func(*bolt.Tx) error) error {
	db, err := bolt.Open(s.journalPath(), 0600, &bolt.Options{Timeout: time.Second, ReadOnly: readonly})
	if err != nil {
		return fmt.Errorf("ERR_OPERATION_JOURNAL: %w", err)
	}
	defer db.Close()
	check := func(tx *bolt.Tx) error {
		b := tx.Bucket(bindingBucket)
		if b == nil || string(b.Get([]byte("state_id"))) != s.data.StateID || string(b.Get([]byte("account"))) != s.data.Account {
			return fmt.Errorf("ERR_STATE_INCOMPATIBLE: operation journal binding mismatch")
		}
		return fn(tx)
	}
	if readonly {
		return db.View(check)
	}
	return db.Update(check)
}

func saveReceipt(tx *bolt.Tx, op *operationRecord) error {
	encoded, err := json.Marshal(op)
	if err != nil {
		return err
	}
	receipts, counts := tx.Bucket(receiptBucket), tx.Bucket(countBucket)
	changeCount := func(status string, delta int64) error {
		old := counts.Get([]byte(status))
		n := uint64(0)
		if len(old) == 8 {
			n = binary.BigEndian.Uint64(old)
		}
		if delta < 0 && n > 0 {
			n--
		} else if delta > 0 {
			n++
		}
		value := make([]byte, 8)
		binary.BigEndian.PutUint64(value, n)
		return counts.Put([]byte(status), value)
	}
	if old := receipts.Get([]byte(op.ID)); old != nil {
		var prior operationRecord
		if err := json.Unmarshal(old, &prior); err != nil {
			return err
		}
		if err := changeCount(prior.Status, -1); err != nil {
			return err
		}
	}
	if err := changeCount(op.Status, 1); err != nil {
		return err
	}
	if err := receipts.Put([]byte(op.ID), encoded); err != nil {
		return err
	}
	if op.Fingerprint != "" {
		index := tx.Bucket(fingerprintBucket)
		// A safe failed attempt must never hide a successful duplicate.
		priorID := index.Get([]byte(op.Fingerprint))
		keepPrior := false
		if op.Status == "not_applied" && priorID != nil && string(priorID) != op.ID {
			var prior operationRecord
			if err := json.Unmarshal(receipts.Get(priorID), &prior); err != nil {
				return err
			}
			keepPrior = prior.Status != "not_applied"
		}
		if !keepPrior {
			if err := index.Put([]byte(op.Fingerprint), []byte(op.ID)); err != nil {
				return err
			}
		}
	}
	if op.Status == "unknown" || op.Status == "in_flight" {
		return tx.Bucket(pendingBucket).Put([]byte(op.ID), encoded)
	}
	return tx.Bucket(pendingBucket).Delete([]byte(op.ID))
}

func (s *stateStore) initJournal(legacy bool) error {
	if !legacy {
		if _, err := os.Stat(s.journalPath()); err != nil {
			return fmt.Errorf("ERR_STATE_LOST: indexed operation journal is required: %w", err)
		}
	} else {
		db, err := bolt.Open(s.journalPath(), 0600, &bolt.Options{Timeout: time.Second})
		if err != nil {
			return err
		}
		err = db.Update(func(tx *bolt.Tx) error {
			for _, name := range [][]byte{bindingBucket, receiptBucket, fingerprintBucket, pendingBucket, countBucket} {
				if _, err := tx.CreateBucketIfNotExists(name); err != nil {
					return err
				}
			}
			b := tx.Bucket(bindingBucket)
			if id := b.Get([]byte("state_id")); id != nil && (string(id) != s.data.StateID || string(b.Get([]byte("account"))) != s.data.Account) {
				return fmt.Errorf("ERR_STATE_INCOMPATIBLE: journal belongs to another state")
			}
			if err := b.Put([]byte("state_id"), []byte(s.data.StateID)); err != nil {
				return err
			}
			if err := b.Put([]byte("account"), []byte(s.data.Account)); err != nil {
				return err
			}
			ids := make([]string, 0, len(s.data.Operations))
			for id := range s.data.Operations {
				ids = append(ids, id)
			}
			sort.Strings(ids)
			for _, id := range ids {
				op := s.data.Operations[id]
				if op == nil || op.ID != id {
					return fmt.Errorf("ERR_STATE_CORRUPT: invalid operation receipt")
				}
				if err := saveReceipt(tx, op); err != nil {
					return err
				}
			}
			return nil
		})
		closeErr := db.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
	}
	// The disk index, not the JSON's former history copy, owns operation status.
	s.data.Operations = map[string]*operationRecord{}
	return s.journal(false, func(tx *bolt.Tx) error {
		pending := tx.Bucket(pendingBucket)
		if err := pending.ForEach(func(id, raw []byte) error {
			var op operationRecord
			if err := json.Unmarshal(raw, &op); err != nil {
				return err
			}
			s.data.Operations[string(id)] = &op
			return nil
		}); err != nil {
			return err
		}
		for _, op := range s.data.Operations {
			if op.Status == "in_flight" {
				op.Status, op.UpdatedAt = "unknown", time.Now()
				if err := saveReceipt(tx, op); err != nil {
					return err
				}
			}
		}
		return nil
	})
}

func (s *stateStore) receipt(id, fingerprint string) (*operationRecord, error) {
	var op *operationRecord
	err := s.journal(true, func(tx *bolt.Tx) error {
		key := []byte(id)
		if fingerprint != "" {
			key = tx.Bucket(fingerprintBucket).Get([]byte(fingerprint))
		}
		if raw := tx.Bucket(receiptBucket).Get(key); raw != nil {
			op = &operationRecord{}
			return json.Unmarshal(raw, op)
		}
		return nil
	})
	return op, err
}

func (s *stateStore) saveOperation(op *operationRecord) error {
	if err := s.journal(false, func(tx *bolt.Tx) error { return saveReceipt(tx, op) }); err != nil {
		return err
	}
	if op.Status == "in_flight" || op.Status == "unknown" {
		s.data.Operations[op.ID] = op
	} else {
		delete(s.data.Operations, op.ID)
	}
	return atomicJSON(s.path, s.data)
}

func (s *stateStore) operationCounts() (map[string]int, error) {
	counts := map[string]int{}
	err := s.journal(true, func(tx *bolt.Tx) error {
		return tx.Bucket(countBucket).ForEach(func(k, v []byte) error {
			if len(v) != 8 {
				return fmt.Errorf("ERR_STATE_CORRUPT: receipt count")
			}
			counts[string(k)] = int(binary.BigEndian.Uint64(v))
			return nil
		})
	})
	return counts, err
}

// Called offline with the process lock held. Export current receipts, including
// writes since upgrade; restoring an old snapshot could otherwise lose dedup.
func (s *stateStore) exportLegacy() error {
	f, err := os.CreateTemp(filepath.Dir(s.path), ".legacy-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if err := f.Chmod(0600); err != nil {
		return err
	}
	copy := s.data
	copy.Version = 1
	copy.Operations = nil
	raw, err := json.Marshal(copy)
	if err != nil {
		return err
	}
	// Replace operations:null without building a full history map in memory.
	var header map[string]json.RawMessage
	if err := json.Unmarshal(raw, &header); err != nil {
		return err
	}
	delete(header, "operations")
	raw, err = json.Marshal(header)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(raw[:len(raw)-1], []byte(",\"operations\":{")...)); err != nil {
		return err
	}
	first := true
	err = s.journal(true, func(tx *bolt.Tx) error {
		return tx.Bucket(receiptBucket).ForEach(func(k, v []byte) error {
			if !first {
				if _, err := f.WriteString(","); err != nil {
					return err
				}
			}
			first = false
			key, _ := json.Marshal(string(k))
			if _, err := f.Write(append(key, ':')); err != nil {
				return err
			}
			_, err := f.Write(v)
			return err
		})
	})
	if err != nil {
		return err
	}
	if _, err := f.WriteString("}}\n"); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(f.Name(), s.path); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(s.path))
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
