use crate::Key;
use crate::{DBTranslationLayer, Value};
use anyhow::Result;
use anyhow::anyhow;
use redis::{Client, Commands, Connection, Pipeline};
use std::cell::{Cell, RefCell};

pub struct Redis {
    _client: Client,
    conn: RefCell<Connection>,
    pipeline: RefCell<Pipeline>,
    pending: Cell<usize>,
    pipeline_depth: usize,
}

fn parse_pipeline_depth(config_string: Option<&str>) -> Result<usize> {
    let Some(config_string) = config_string else {
        return Ok(1);
    };
    for opt in config_string.split(';') {
        let opt = opt.trim();
        if let Some(value) = opt.strip_prefix("pipeline=") {
            return value
                .parse::<usize>()
                .map_err(|e| anyhow!("Could not parse redis pipeline depth: {:#?}", e));
        }
    }
    Ok(1)
}

impl Redis {
    pub fn new(endpoint: Option<&str>, config_string: Option<&str>) -> Result<Self> {
        let endpoint = endpoint.unwrap_or("redis://127.0.0.1/");
        let client = Client::open(endpoint)?;
        let conn = RefCell::new(client.get_connection()?);
        let pipeline_depth = parse_pipeline_depth(config_string)?.max(1);

        Ok(Self {
            _client: client,
            conn,
            pipeline: RefCell::new(redis::pipe()),
            pending: Cell::new(0),
            pipeline_depth,
        })
    }

    fn flush(&self) -> Result<()> {
        if self.pending.get() == 0 {
            return Ok(());
        }
        let mut pipe = self.pipeline.borrow_mut();
        let mut conn = self.conn.borrow_mut();
        let _: () = pipe.query(&mut *conn)?;
        pipe.clear();
        self.pending.set(0);

        Ok(())
    }

    fn buffer(&self, build: impl FnOnce(&mut Pipeline)) -> Result<()> {
        build(&mut self.pipeline.borrow_mut());
        self.pending.set(self.pending.get() + 1);
        if self.pending.get() >= self.pipeline_depth {
            self.flush()?;
        }

        Ok(())
    }
}

impl DBTranslationLayer for Redis {
    fn cleanup(&mut self) -> Result<()> {
        self.flush()
    }

    fn insert(&self, key: &Key, value: &Value) -> Result<()> {
        if self.pipeline_depth <= 1 {
            let _: () = self.conn.borrow_mut().set(key, value)?;
            return Ok(());
        }

        self.buffer(|pipe| {
            pipe.set(key, value).ignore();
        })
    }

    fn update(&self, key: &Key, value: &Value) -> Result<()> {
        if self.pipeline_depth <= 1 {
            let _: () = self.conn.borrow_mut().set(key, value)?;
            return Ok(());
        }

        self.buffer(|pipe| {
            pipe.set(key, value).ignore();
        })
    }

    fn merge(&self, key: &Key, value: &Value) -> Result<()> {
        self.point_query(key)?;
        self.update(key, value)?;

        Ok(())
    }

    fn point_delete(&self, key: &Key) -> Result<()> {
        if self.pipeline_depth <= 1 {
            let _: () = self.conn.borrow_mut().del(key)?;
            return Ok(());
        }

        self.buffer(|pipe| {
            pipe.del(key).ignore();
        })
    }

    fn point_query(&self, key: &Key) -> Result<()> {
        if self.pipeline_depth <= 1 {
            let _: () = self.conn.borrow_mut().get(key)?;
            return Ok(());
        }

        self.buffer(|pipe| {
            pipe.get(key).ignore();
        })
    }

    fn range_query(&self, _start_key: &Key, _end_key: &Value) -> Result<()> {
        // NOTE: Redis does not sort its keyspace, so I'm not sure we should allow scanning
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_query_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_delete(&self, _start_key: &Key, _end_key: &Key) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }

    fn range_delete_count(&self, _start_key: &Key, _range: usize) -> Result<()> {
        return Err(anyhow!("Redis does not support range operations"));
    }
}
